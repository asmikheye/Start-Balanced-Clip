import os
import sys
import uuid
import json
import shutil
import asyncio
import logging
from dotenv import load_dotenv
from typing import Dict, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("clippyme")

# The pinned dependency set (faster-whisper, mediapipe, etc.) is only tested on
# Python 3.11+. Warn loudly rather than failing with a cryptic import error on
# an older interpreter.
if sys.version_info < (3, 11):
    logger.warning(
        "ClippyMe requires Python 3.11+. Detected %s — imports may fail or behave unexpectedly.",
        ".".join(map(str, sys.version_info[:3])),
    )

from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from clippyme.domain.job_results import ALLOWED_REFRAME_MODES, build_main_cmd, canonical_reframe_mode
from clippyme.domain.compose import compose_layers
from clippyme.domain.reframe_service import run_reframe
from clippyme.domain.errors import ClippyMeError
from clippyme.domain.uploads import stream_upload_within_limit, FileTooLarge
from clippyme.domain.clip_endpoints import run_smart_cut, restore_job_from_disk
from clippyme.domain.clip_resolve import resolve_clip
from clippyme.domain import job_control
from clippyme.domain.job_actions import cancel_job_action, stop_job_action
from clippyme.domain.job_journal import JOURNAL_FILENAME, make_journal_writer, recover_jobs
from clippyme.domain.job_runner import make_run_job
from clippyme.domain.job_submission import QueueFullError, submit_job
from clippyme.domain.publish_service import publish_clip_flow
from clippyme.api.schemas import (
    BatchRequest,
    ComposeRequest,
    EditAIRequest,
    LiveMonitorPublishingRequest,
    LiveMonitorStartRequest,
    LiveMonitorStopRequest,
    ProcessRequest,
    PublishRequest,
    ReframeRequest,
    _validate_drop_ranges,
)
from clippyme.api.security import (
    ALLOWED_ORIGINS,
    enforce_api_token,
    enforce_rate_limit,
    require_trusted_config_request,
)
from clippyme.storage.config_store import (
    load_persistent_config,
    save_persistent_config,
    load_zernio_config,
)
from clippyme.domain.job_worker import make_workers
from clippyme.domain.history_service import scan_history, is_valid_job_id
from clippyme.api.config_routes import router as config_router
from clippyme.api.queue_routes import router as queue_router

load_dotenv()

# Constants
UPLOAD_DIR = "uploads"
OUTPUT_DIR = "output"
DATA_DIR = "data"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# Initial load to env
save_persistent_config(load_persistent_config())

# Configuration
# Default to 1 if not set, but user can set higher for powerful servers
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "5"))
MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", "16384"))
# Default retention is 30 days — the frontend History tab is the
# authoritative source of truth for what the user considers "done".
# Aggressive auto-purge was destroying clips behind the user's back
# (jobs older than 1 hour vanished on the next cleanup tick, meaning
# every docker restart + 5 min wait blew away yesterday's work).
# Override via env: JOB_RETENTION_SECONDS (0 disables auto-purge).
JOB_RETENTION_SECONDS = int(os.environ.get("JOB_RETENTION_SECONDS", str(30 * 86400)))

# Application State
job_queue = asyncio.Queue(maxsize=50)
jobs: Dict[str, Dict] = {}
# Semaphore to limit concurrency to MAX_CONCURRENT_JOBS
concurrency_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

# Job journal: persists the ACTIVE jobs to data/jobs_journal.json on every
# status transition so a restart can re-enqueue queued jobs and fail (or
# restore) interrupted ones instead of silently forgetting them.
JOURNAL_PATH = os.path.join(DATA_DIR, JOURNAL_FILENAME)
persist_jobs = make_journal_writer(jobs=jobs, path=JOURNAL_PATH)

# The per-job subprocess runner, bound to the shared jobs dict (thin-handler
# rule: the body lives in clippyme.domain.job_runner).
run_job = make_run_job(jobs=jobs, output_root=OUTPUT_DIR, on_change=persist_jobs)

# Multi-platform content monitor registry: concurrent asyncio tasks (one per
# platform:channel) that detect live streams / new VODs, submit them as normal
# jobs, and auto-publish clips with GLOBAL publish spacing. Bound to the same
# shared job state (thin-handler rule: logic lives in domain.live_monitor).
from clippyme.domain.live_monitor import LiveMonitorRegistry
live_monitor = LiveMonitorRegistry(
    jobs=jobs, job_queue=job_queue, output_dir=OUTPUT_DIR,
    upload_dir=UPLOAD_DIR, on_job_change=persist_jobs,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Recover journalled jobs from the previous server life BEFORE the
    # dispatcher starts: queued jobs are re-enqueued, interrupted ones are
    # marked failed (or restored as completed when their result is on disk).
    # Runs on the event loop (not to_thread): asyncio.Queue.put_nowait is not
    # thread-safe, and the journal is small so the startup pause is negligible.
    try:
        recover_jobs(journal_path=JOURNAL_PATH, jobs=jobs,
                     job_queue=job_queue, output_root=OUTPUT_DIR)
    except Exception:
        logger.exception("Job journal recovery failed — starting with an empty queue")

    cleanup_jobs, process_queue, _run_job_wrapper = make_workers(
        jobs=jobs,
        job_queue=job_queue,
        concurrency_semaphore=concurrency_semaphore,
        run_job=run_job,
        output_dir=OUTPUT_DIR,
        upload_dir=UPLOAD_DIR,
        data_dir=DATA_DIR,
        job_retention_seconds=JOB_RETENTION_SECONDS,
        max_concurrent_jobs=MAX_CONCURRENT_JOBS,
    )
    worker_task = asyncio.create_task(process_queue())
    cleanup_task = asyncio.create_task(cleanup_jobs())
    # Background auto-update for the auto-editor binary used by smartcut.py.
    # Failures are non-fatal — smartcut has an FFmpeg fallback path.
    from clippyme.integrations.auto_editor_updater import background_updater_loop
    ae_updater_task = asyncio.create_task(background_updater_loop())
    # Bring back every monitor that was still marked resume_on_start when the
    # process last went down (durable auto-resume). Never fatal to startup —
    # a per-monitor failure stays visible via its status() instead.
    try:
        await live_monitor.auto_resume()
    except Exception:
        logger.exception("live monitor auto-resume failed")
    yield
    # Stop the live monitor first so its in-flight capture/publish tasks unwind
    # cleanly before we tear down the worker loops they depend on. shutdown()
    # (not stop()) so resume_on_start survives for the next auto-resume.
    try:
        await live_monitor.shutdown()
    except Exception:
        logger.exception("live monitor failed to stop cleanly")
    # Cancel ALL background tasks on shutdown — not just the updater. Leaving
    # the worker/cleanup loops pending blocks uvicorn's graceful exit and logs
    # "Task was destroyed but it is pending!" tracebacks.
    _bg_tasks = (worker_task, cleanup_task, ae_updater_task)
    for _t in _bg_tasks:
        _t.cancel()
    for _t in _bg_tasks:
        try:
            await _t
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("background task raised during shutdown")

app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def _api_token_gate(request: Request, call_next):
    """Optional shared-secret auth for deliberate LAN deployments.

    Active only when CLIPPYME_API_TOKEN is set (default unset = no-op). Guards
    every /api route; the static media mounts (/videos, /thumbnails, /fonts)
    stay IP-open because <video>/<img>/FontFace requests can't attach custom
    headers. HTTPException is converted here because raise inside middleware
    bypasses FastAPI's exception handlers.
    """
    if request.url.path.startswith("/api/") and request.method != "OPTIONS":
        try:
            enforce_api_token(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Add OWASP-recommended hardening headers to every response.

    - nosniff: block MIME-confusion attacks on served media/JSON.
    - frame-ancestors/X-Frame-Options: clickjacking defence.
    - Referrer-Policy: don't leak full URLs (job ids) to third parties.
    - CSP default-src 'none': the API serves JSON + media consumed by the
      separate Vite frontend; it should never itself be a script/HTML host.
    Ref: OWASP Secure Headers Project.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    return response


@app.exception_handler(ClippyMeError)
async def _clippyme_error_handler(request: Request, exc: ClippyMeError):
    """Map domain exceptions to HTTP responses so domain modules don't need
    to import FastAPI's HTTPException."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def _unhandled_error_handler(request: Request, exc: Exception):
    """Catch-all so a stray exception never leaks a traceback / internal path
    to the client. FastAPI's HTTPException is handled separately and is not
    affected by this."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Gemini-Key", "X-API-Token"],
)

# Mount static files for serving videos.
# The output directory also holds *_metadata.json (full transcripts + AI
# analysis) and source_*.mp4 (the raw 16:9 slices). Those are internal
# artifacts and must NOT be publicly downloadable — only the rendered clips,
# composed clips, covers and thumbnails are user-facing. SafeStaticFiles
# 404s the sensitive patterns while serving everything else as before.
class SafeStaticFiles(StaticFiles):
    # Only user-facing media belongs under /videos. Metadata, transcript
    # sidecars and temporary renderer files can contain full transcripts,
    # prompts, local paths or source footage and must never be served.
    _BLOCKED_SUFFIXES = (".json", ".ass", ".srt", ".tmp", ".part")
    _BLOCKED_PREFIXES = ("source_", ".")

    async def get_response(self, path, scope):
        leaf = os.path.basename(path.replace("\\", "/"))
        if leaf.endswith(self._BLOCKED_SUFFIXES) or leaf.startswith(self._BLOCKED_PREFIXES):
            raise HTTPException(status_code=404, detail="Not found")
        return await super().get_response(path, scope)


app.mount("/videos", SafeStaticFiles(directory=OUTPUT_DIR), name="videos")

# Mount static files for serving thumbnails
THUMBNAILS_DIR = os.path.join(OUTPUT_DIR, "thumbnails")
os.makedirs(THUMBNAILS_DIR, exist_ok=True)
app.mount("/thumbnails", StaticFiles(directory=THUMBNAILS_DIR), name="thumbnails")

# Mount static files for serving fonts (used by subtitle preview in frontend)
app.mount("/fonts", StaticFiles(directory="fonts"), name="fonts")

# Config-family routes (keys, cookies, fonts, logo, zernio) live in their own
# router — they touch none of the job runtime state, so keeping them out of
# app.py lets this module stay focused on the job lifecycle.
app.include_router(config_router)
app.include_router(queue_router)


@app.get("/")
async def root():
    return {"status": "online", "message": "ClippyMe API is running"}

@app.get("/api/health")
async def health():
    return {"status": "healthy"}

@app.post("/api/process")
async def process_endpoint(
    request: Request,
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None)
):
    # CSRF/origin gate so a malicious page can't trigger compute jobs against
    # a locally-running backend. Same trust model as the config endpoints.
    require_trusted_config_request(request)
    # ~20 single-job submissions/min per client; compute-heavy, so throttle.
    enforce_rate_limit(request, "process", capacity=20, refill_per_sec=20 / 60)
    api_key = request.headers.get("X-Gemini-Key")
    if not api_key:
        raise HTTPException(status_code=400, detail="Missing X-Gemini-Key header")

    # Handle JSON body via ProcessRequest for URL payloads. Pydantic
    # enforces the reframe_mode regex and the instructions length cap
    # before we hand anything to build_main_cmd. Multipart uploads keep
    # the manual form extraction because the file streaming path is
    # already using FastAPI's File/Form dependencies.
    instructions = None
    reframe_mode = None
    letterbox_zoom = None
    aspect = None
    language = None
    no_zoom = False
    skip_analysis = False
    model = None
    create_subtitles = None
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            validated = ProcessRequest.model_validate(body or {})
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.errors())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        url = validated.url
        instructions = validated.instructions
        reframe_mode = validated.reframe_mode
        letterbox_zoom = validated.letterbox_zoom
        aspect = validated.aspect
        language = validated.language
        no_zoom = bool(validated.no_zoom)
        skip_analysis = bool(validated.skip_analysis)
        model = validated.model
        create_subtitles = validated.create_subtitles

    # For multipart/form-data uploads, extract reframe_mode + language from form fields
    if "multipart/form-data" in content_type:
        form = await request.form()
        reframe_mode = form.get("reframe_mode", reframe_mode)
        letterbox_zoom = form.get("letterbox_zoom", letterbox_zoom)
        aspect = form.get("aspect", aspect)
        language = form.get("language", language)
        # Also honour the optional instructions field in multipart mode
        # so drag-and-drop uploads can pass AI directives just like URL
        # submissions (was previously ignored).
        instructions = form.get("instructions", instructions)
        no_zoom = str(form.get("no_zoom", "")).lower() in {"1", "true", "yes"} or no_zoom
        skip_analysis = str(form.get("skip_analysis", "")).lower() in {"1", "true", "yes"} or skip_analysis
        model = form.get("model", model) or None
        raw_create_subtitles = form.get("create_subtitles")
        if raw_create_subtitles:
            try:
                create_subtitles = json.loads(str(raw_create_subtitles))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise HTTPException(status_code=400, detail="Invalid create_subtitles JSON") from exc
        # Validate the multipart values through the same schema for
        # consistency — we drop the url requirement since we're using
        # an uploaded file path.
        try:
            validated_upload = ProcessRequest.model_validate({
                "url": "https://upload.invalid/local",
                "reframe_mode": reframe_mode or None,
                "letterbox_zoom": letterbox_zoom or None,
                "aspect": aspect or None,
                "language": language or None,
                "instructions": instructions or None,
                "no_zoom": no_zoom,
                "skip_analysis": skip_analysis,
                "model": model or None,
                "create_subtitles": create_subtitles,
            })
            create_subtitles = validated_upload.create_subtitles
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.errors())

    if not url and not file:
        raise HTTPException(status_code=400, detail="Must provide URL or File")

    job_id = str(uuid.uuid4())
    job_output_dir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(job_output_dir, exist_ok=True)
    
    env = os.environ.copy()
    env["GEMINI_API_KEY"] = api_key

    input_path = None
    if not url:
        # Save uploaded file with a server-generated name. We deliberately
        # discard the client-supplied filename (path traversal risk) and only
        # preserve a sanitized extension whitelisted to known media formats.
        raw_ext = os.path.splitext(file.filename or "")[1].lower()
        allowed_ext = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
        if raw_ext not in allowed_ext:
            # Reject unknown extensions explicitly instead of silently
            # treating them as .mp4 (which produced confusing downstream
            # ffmpeg failures on non-video uploads).
            shutil.rmtree(job_output_dir, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail="Unsupported file type. Allowed: .mp4, .mov, .mkv, .webm, .m4v, .avi",
            )
        input_path = os.path.join(UPLOAD_DIR, f"{job_id}{raw_ext}")
        try:
            upload_size = await stream_upload_within_limit(
                file, input_path, MAX_FILE_SIZE_MB * 1024 * 1024
            )
        except FileTooLarge as exc:
            await asyncio.to_thread(shutil.rmtree, job_output_dir, True)
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except BaseException:
            await asyncio.to_thread(shutil.rmtree, job_output_dir, True)
            raise
        if upload_size == 0:
            await asyncio.to_thread(shutil.rmtree, job_output_dir, True)
            try:
                await asyncio.to_thread(os.remove, input_path)
            except FileNotFoundError:
                pass
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        cmd = build_main_cmd(
            url=url,
            input_path=input_path,
            output_dir=job_output_dir,
            instructions=instructions,
            reframe_mode=reframe_mode,
            letterbox_zoom=letterbox_zoom,
            aspect=aspect,
            cookies_path=os.path.join("data", "cookies.txt"),
            language=language,
            no_zoom=no_zoom,
            skip_analysis=skip_analysis,
            model=model,
        )
    except ValueError as exc:
        await asyncio.to_thread(shutil.rmtree, job_output_dir, True)
        if input_path:
            try:
                await asyncio.to_thread(os.remove, input_path)
            except FileNotFoundError:
                pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Queue-full rollback removes both the output directory and uploaded input.
    await submit_job(
        jobs=jobs, job_queue=job_queue, job_id=job_id,
        cmd=cmd, env=env, job_output_dir=job_output_dir,
        on_change=persist_jobs, cleanup_paths=(input_path,), input_path=input_path,
        create_subtitles=create_subtitles,
    )

    return {"job_id": job_id, "status": "queued"}


@app.post("/api/batch")
async def batch_process(req: BatchRequest, request: Request):
    """Submit multiple URLs for batch processing. Each URL becomes a separate job."""
    require_trusted_config_request(request)
    # Each batch can enqueue up to 20 jobs, so limit batch calls more tightly.
    enforce_rate_limit(request, "batch", capacity=10, refill_per_sec=10 / 60)
    api_key = request.headers.get("X-Gemini-Key")
    if not api_key:
        raise HTTPException(status_code=400, detail="Missing X-Gemini-Key header")

    batch_jobs = []

    for url in req.urls:
        url = url.strip()
        if not url:
            continue

        job_id = str(uuid.uuid4())
        job_output_dir = os.path.join(OUTPUT_DIR, job_id)
        os.makedirs(job_output_dir, exist_ok=True)

        try:
            cmd = build_main_cmd(
                url=url,
                output_dir=job_output_dir,
                instructions=req.instructions,
                reframe_mode=req.reframe_mode,
                letterbox_zoom=req.letterbox_zoom,
                aspect=getattr(req, "aspect", None),
                cookies_path=os.path.join("data", "cookies.txt"),
                language=getattr(req, "language", None),
                no_zoom=bool(getattr(req, "no_zoom", False)),
                skip_analysis=bool(getattr(req, "skip_analysis", False)),
                model=getattr(req, "model", None),
            )
        except ValueError as exc:
            # This item's output dir was already created above but it never
            # made it into `jobs` — clean it up so a bad URL can't orphan a dir.
            await asyncio.to_thread(shutil.rmtree, job_output_dir, True)
            raise HTTPException(status_code=400, detail=str(exc))

        env = os.environ.copy()
        env["GEMINI_API_KEY"] = api_key

        try:
            await submit_job(
                jobs=jobs, job_queue=job_queue, job_id=job_id,
                cmd=cmd, env=env, job_output_dir=job_output_dir, batch=True,
                on_change=persist_jobs,
                create_subtitles=req.create_subtitles,
            )
            batch_jobs.append({"url": url, "job_id": job_id})
        except QueueFullError:
            # Only the item that failed to enqueue was cleaned up (by
            # submit_job) — already enqueued jobs stay running. Stop adding
            # more; the queue is full. Mirrors the single /api/process path.
            break

    if not batch_jobs:
        raise HTTPException(status_code=400, detail="No valid URLs provided or queue is full.")

    return {"jobs": batch_jobs, "total": len(batch_jobs)}


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    if not is_valid_job_id(job_id):
        raise HTTPException(status_code=400, detail="Invalid job ID")
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    return {
        "status": job['status'],
        "logs": job.get('logs', [])[-500:],
        "result": job.get('result')
    }

@app.post("/api/cancel/{job_id}")
async def cancel_job(job_id: str, request: Request):
    """Cancel a running job by killing its subprocess."""
    require_trusted_config_request(request)
    if not is_valid_job_id(job_id):
        raise HTTPException(status_code=400, detail="Invalid job ID")
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        return await cancel_job_action(job_id, jobs[job_id])
    finally:
        persist_jobs()


@app.post("/api/pause/{job_id}")
async def pause_job(job_id: str, request: Request):
    """Suspend a running job's process tree (SIGSTOP/SuspendThread via psutil)."""
    require_trusted_config_request(request)
    if not is_valid_job_id(job_id):
        raise HTTPException(status_code=400, detail="Invalid job ID")
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    if not job_control.can_pause(job['status']):
        raise HTTPException(status_code=400, detail="Job cannot be paused")

    proc = job.get('process')
    if not (proc and proc.poll() is None):
        raise HTTPException(status_code=409, detail="Job has no running process")
