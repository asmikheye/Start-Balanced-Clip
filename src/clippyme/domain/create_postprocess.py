"""Create-time layer post-processing.

The main pipeline deliberately owns source acquisition, analysis and reframing.
Create-selected presentation layers are applied only after those base clips are
stable. For now this module auto-applies SUBTITLES only; Hook and the other
layers remain untouched until explicitly enabled for Create-time rendering.
"""
from __future__ import annotations

import json
import os

from clippyme.domain.clip_resolve import resolve_clip
from clippyme.domain.compose import compose_layers
from clippyme.domain.job_artifacts import find_job_metadata_path


def _write_metadata_atomic(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


async def apply_create_subtitles(
    *,
    job_id: str,
    output_root: str,
    subtitle_params: dict | None,
) -> dict:
    """Burn the Create subtitle recipe onto every rendered clip.

    The raw/reframed clip stays untouched. Each successful composed artifact is
    recorded on the clip as create_composed_filename so result/history views
    can default to the captioned version while Edit can still fall back to raw.

    Per-clip failures are isolated: one bad caption burn never destroys the
    other finished clips. The returned summary is persisted into metadata and
    surfaced by the job runner.
    """
    params = dict(subtitle_params or {})
    if not params:
        return {"requested": 0, "applied": 0, "failed": 0, "errors": []}

    metadata_path = find_job_metadata_path(job_id, output_root)
    with open(metadata_path, encoding="utf-8") as handle:
        metadata = json.load(handle)

    clips = metadata.get("shorts") or []
    errors: list[dict] = []
    applied = 0

    for index, _clip in enumerate(clips):
        try:
            resolved = resolve_clip(job_id, index, output_root)
            composed_filename = await compose_layers(
                base_clip=resolved.clip_path,
                job_dir=resolved.job_dir,
                clip_index=index,
                metadata=resolved.metadata,
                clip_info=resolved.clip_info,
                toggles={
                    "smartcut": False,
                    "hook": False,
                    "subtitles": True,
                    "logo": False,
                    "grade": False,
                    "banner": False,
                },
                hook_params={},
                subtitle_params=params,
                logo_params={},
                grade_params={},
                banner_params={},
                drop_ranges=[],
            )
            clips[index]["create_composed_filename"] = composed_filename
            applied += 1
        except Exception as exc:  # keep the raw clip usable
            errors.append({"clip_index": index, "error": str(exc)[:300]})

    metadata["create_recipe"] = {"subtitles": params}
    summary = {
        "requested": len(clips),
        "applied": applied,
        "failed": len(errors),
        "errors": errors,
    }
    metadata["create_postprocess"] = summary
    _write_metadata_atomic(metadata_path, metadata)
    return summary
