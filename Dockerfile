# ClippyMe — Multi-platform Dockerfile
#
# CPU / Apple Silicon (default):  docker compose up --build
# NVIDIA GPU:                     docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build

ARG GPU_RUNTIME=cpu

# ============================================================
# Stage 2a: NVIDIA CUDA runtime (x86_64 only)
# ============================================================
# Ubuntu 24.04 (glibc 2.39) — the auto-editor releases (v29+) are built on
# 24.04-era runners and hard-require GLIBC_2.38; the old 22.04 base (2.35)
# loaded the binary but it died at runtime with "GLIBC_2.38 not found", so
# Smart Cut silently fell back to FFmpeg. CUDA stays on the 12.x line so the
# torch cu12 wheels and the NVIDIA pip stack keep matching.
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04 AS runtime-nvidia

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    software-properties-common && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3.11-dev python3.11-distutils \
    ffmpeg libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libcairo2 \
    curl unzip ca-certificates gosu && \
    curl -fsSL https://deno.land/install.sh | sh -s v2.8.3 && \
    mv /root/.deno/bin/deno /usr/local/bin/ && \
    rm -rf /root/.deno && \
    ln -sf /usr/bin/python3.11 /usr/bin/python && \
    ln -sf /usr/bin/python3.11 /usr/bin/python3 && \
    curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 && \
    # software-properties-common (used above only for add-apt-repository) pulls
    # Debian's python3-* packages (pyparsing, six, httplib2, gi, apt, …) into
    # the shared /usr/lib/python3/dist-packages. python3.11's pip sees those and
    # fails to REPLACE any it needs ("no RECORD file") — e.g. pyparsing when
    # requirements.lock pins a different version. The app only uses python3.11
    # pip-installed packages, so drop the Debian (python3.12) shared dir.
    rm -rf /usr/lib/python3/dist-packages && \
    rm -rf /var/lib/apt/lists/* && \
    # Install a PINNED auto-editor Nim binary (v30.x track) with sha256
    # verification, so a re-tagged/tampered GitHub asset can't slip in at build
    # time. The optional runtime updater keeps it fresh only when explicitly
    # enabled; bump AE_VERSION + the digests together when updating the pin.
    AE_VERSION=30.5.0 && \
    ARCH=$(uname -m) && \
    case "$ARCH" in \
      x86_64)  AE_ASSET=auto-editor-linux-x86_64;  AE_SHA=673e69b096d740736364f34669e864505294441f5ec9188642b33e24b07cf147 ;; \
      aarch64) AE_ASSET=auto-editor-linux-aarch64; AE_SHA=0c54d2cbc617fd369dae434eb62156557877da47c60fd8b2018b65b481166a4e ;; \
      *) echo "Unsupported arch $ARCH for auto-editor binary"; exit 1 ;; \
    esac && \
    curl -fsSL -o /usr/local/bin/auto-editor \
      "https://github.com/WyattBlue/auto-editor/releases/download/${AE_VERSION}/$AE_ASSET" && \
    echo "$AE_SHA  /usr/local/bin/auto-editor" | sha256sum -c - && \
    chmod +x /usr/local/bin/auto-editor && \
    (/usr/local/bin/auto-editor --version || echo "auto-editor version check failed (non-fatal)")

ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# ============================================================
# Stage 2b: CPU runtime (multi-arch: amd64, arm64, Apple Silicon)
# ============================================================
# Ubuntu 24.04 (glibc 2.39) instead of the old python:3.11-slim (Debian
# bookworm, glibc 2.36) — auto-editor needs GLIBC_2.38 (see runtime-nvidia).
# python 3.11 is kept (via deadsnakes) so the pinned CV/ML wheels (torch,
# mediapipe 0.10.14, …) are unchanged; the pip bootstrap mirrors runtime-nvidia.
FROM ubuntu:24.04 AS runtime-cpu

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    software-properties-common && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3.11-dev python3.11-distutils \
    ffmpeg libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libcairo2 \
    curl unzip ca-certificates gosu && \
    curl -fsSL https://deno.land/install.sh | sh -s v2.8.3 && \
    mv /root/.deno/bin/deno /usr/local/bin/ && \
    rm -rf /root/.deno && \
    ln -sf /usr/bin/python3.11 /usr/bin/python && \
    ln -sf /usr/bin/python3.11 /usr/bin/python3 && \
    curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 && \
    # software-properties-common (used above only for add-apt-repository) pulls
    # Debian's python3-* packages (pyparsing, six, httplib2, gi, apt, …) into
    # the shared /usr/lib/python3/dist-packages. python3.11's pip sees those and
    # fails to REPLACE any it needs ("no RECORD file") — e.g. pyparsing when
    # requirements.lock pins a different version. The app only uses python3.11
    # pip-installed packages, so drop the Debian (python3.12) shared dir.
    rm -rf /usr/lib/python3/dist-packages && \
    rm -rf /var/lib/apt/lists/* && \
    # Install a PINNED auto-editor Nim binary (v30.x track) with sha256
    # verification, so a re-tagged/tampered GitHub asset can't slip in at build
    # time. The optional runtime updater keeps it fresh only when explicitly
    # enabled; bump AE_VERSION + the digests together when updating the pin.
    AE_VERSION=30.5.0 && \
    ARCH=$(uname -m) && \
    case "$ARCH" in \
      x86_64)  AE_ASSET=auto-editor-linux-x86_64;  AE_SHA=673e69b096d740736364f34669e864505294441f5ec9188642b33e24b07cf147 ;; \
      aarch64) AE_ASSET=auto-editor-linux-aarch64; AE_SHA=0c54d2cbc617fd369dae434eb62156557877da47c60fd8b2018b65b481166a4e ;; \
      *) echo "Unsupported arch $ARCH for auto-editor binary"; exit 1 ;; \
    esac && \
    curl -fsSL -o /usr/local/bin/auto-editor \
      "https://github.com/WyattBlue/auto-editor/releases/download/${AE_VERSION}/$AE_ASSET" && \
    echo "$AE_SHA  /usr/local/bin/auto-editor" | sha256sum -c - && \
    chmod +x /usr/local/bin/auto-editor && \
    (/usr/local/bin/auto-editor --version || echo "auto-editor version check failed (non-fatal)")

# ============================================================
# Stage 3: Final image
# ============================================================
FROM runtime-${GPU_RUNTIME} AS final

WORKDIR /app
ENV PYTHONUNBUFFERED=1
# /app/data/bin is the writable location where auto_editor_updater.py drops
# fresh auto-editor binaries at runtime. Prepend it so it shadows the
# system-wide install in /usr/local/bin when a newer version is available.
ENV PATH=/app/data/bin:$PATH

# Install Python deps. The generated requirements.lock includes the full
# resolver output for audit/reproducibility, including the GPU packages that
# PyTorch selected. Docker deliberately installs the shared base lock without
# dependencies, then installs exactly one PyTorch variant. This keeps CPU
# images free of CUDA 13 packages and keeps NVIDIA images on the cu126 line.
#
# Speaker diarization on the Whisper path is OPT-IN via ENABLE_WHISPER_DIARIZE.
# pyannote.audio pulls ~500 MB of deps (pytorch-lightning, speechbrain,
# torchaudio extras) and requires accepting the pyannote/speaker-diarization-3.1
# license on HuggingFace, so we keep it out of the default image.
# Build with:   docker compose build --build-arg ENABLE_WHISPER_DIARIZE=1
ARG GPU_RUNTIME
ARG ENABLE_WHISPER_DIARIZE=0
# Install from the fully-pinned core lock plus the explicitly pinned auxiliary
# CLI/rendering tools. requirements.txt is copied for reference/diagnostics.
COPY requirements.lock requirements.base.lock requirements.txt requirements-runtime-tools.txt ./
# BuildKit cache mount: pip's download cache lives in the mount (shared across
# rebuilds) and is NOT baked into the image layer.
# PyPI's torch 2.11 wheel uses CUDA 13, which excludes Pascal GPUs such as
# the GTX 1050 Ti; the cu126 wheel retains sm_61 support on NVIDIA builds.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install --no-deps -r requirements.base.lock && \
    pip install -r requirements-runtime-tools.txt && \
    if [ "$GPU_RUNTIME" = "nvidia" ]; then \
        pip install --no-deps 'torch==2.11.0+cu126' 'torchvision==0.26.0+cu126' \
            --index-url https://download.pytorch.org/whl/cu126 \
            --extra-index-url https://pypi.org/simple && \
        pip install 'nvidia-cublas-cu12==12.6.4.1' && \
        SITE=$(python -c "import site; print(site.getsitepackages()[0])") && \
        echo "$SITE/nvidia/cublas/lib" > /etc/ld.so.conf.d/nvidia-pip.conf && \
        echo "$SITE/nvidia/cudnn/lib" >> /etc/ld.so.conf.d/nvidia-pip.conf && \
        ldconfig 2>/dev/null || true; \
    else \
        pip install --no-deps 'torch==2.11.0+cpu' 'torchvision==0.26.0+cpu' \
            --index-url https://download.pytorch.org/whl/cpu \
            --extra-index-url https://pypi.org/simple; \
    fi && \
    if [ "$ENABLE_WHISPER_DIARIZE" = "1" ]; then \
        pip install 'pyannote.audio>=3.1'; \
    fi

# NOTE: do NOT `pip install --upgrade yt-dlp` here — that would un-pin yt-dlp
# from requirements.lock and pull whatever the latest unreviewed release is at
# build time. The reviewed floor is yt-dlp>=2026.6.9,<2027; update it by
# regenerating the lock.

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser && \
    mkdir -p /app/uploads /app/output /app/data /app/data/bin /tmp/Ultralytics && \
    chown -R appuser:appuser /app /tmp/Ultralytics

USER appuser

# Pre-download YOLO model
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

COPY --chown=appuser:appuser . .

# Install the clippyme package itself (src-layout) so that
# `python -m clippyme.pipeline.main` and `uvicorn clippyme.api.app:app` resolve.
USER root
RUN pip install --no-cache-dir -e .

# Entrypoint normalizes ownership of the (bind-mountable) data/ dir as root,
# then drops to appuser via gosu before launching the server. Copied to a
# path OUTSIDE /app so the `.:/app` bind mount can't shadow it.
COPY --chmod=0755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/ || exit 1

CMD ["uvicorn", "clippyme.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
