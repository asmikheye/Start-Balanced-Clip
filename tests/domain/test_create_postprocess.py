import asyncio
import json
from types import SimpleNamespace

from clippyme.domain import create_postprocess


def test_apply_create_subtitles_records_composed_files_and_recipe(monkeypatch, tmp_path):
    job_id = "job-1"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    metadata_path = job_dir / "video_metadata.json"
    metadata_path.write_text(json.dumps({
        "shorts": [
            {"start": 1, "end": 4, "clip_filename": "a_clip_1.mp4"},
            {"start": 5, "end": 9, "clip_filename": "b_clip_2.mp4"},
        ],
        "transcript": {"segments": []},
    }), encoding="utf-8")
    (job_dir / "a_clip_1.mp4").write_bytes(b"a")
    (job_dir / "b_clip_2.mp4").write_bytes(b"b")

    monkeypatch.setattr(
        create_postprocess, "find_job_metadata_path", lambda _job_id, _root: str(metadata_path)
    )

    def fake_resolve(_job_id, index, _root):
        name = ["a_clip_1.mp4", "b_clip_2.mp4"][index]
        clip = {"start": index, "end": index + 3, "clip_filename": name}
        return SimpleNamespace(
            clip_path=str(job_dir / name),
            job_dir=str(job_dir),
            metadata={"shorts": []},
            clip_info=clip,
        )

    monkeypatch.setattr(create_postprocess, "resolve_clip", fake_resolve)
    calls = []

    async def fake_compose(**kwargs):
        calls.append(kwargs)
        return f"captioned_{kwargs['clip_index']}.mp4"

    monkeypatch.setattr(create_postprocess, "compose_layers", fake_compose)

    summary = asyncio.run(create_postprocess.apply_create_subtitles(
        job_id=job_id,
        output_root=str(tmp_path),
        subtitle_params={"mode": "karaoke", "preset": "fire_impact", "font_size": 54},
    ))

    assert summary == {"requested": 2, "applied": 2, "failed": 0, "errors": []}
    assert len(calls) == 2
    assert all(call["toggles"]["subtitles"] is True for call in calls)
    assert all(call["toggles"]["hook"] is False for call in calls)
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert saved["shorts"][0]["create_composed_filename"] == "captioned_0.mp4"
    assert saved["shorts"][1]["create_composed_filename"] == "captioned_1.mp4"
    assert saved["create_recipe"]["subtitles"]["preset"] == "fire_impact"


def test_apply_create_subtitles_keeps_other_clips_when_one_fails(monkeypatch, tmp_path):
    job_id = "job-1"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    metadata_path = job_dir / "video_metadata.json"
    metadata_path.write_text(json.dumps({
        "shorts": [{"clip_filename": "a.mp4"}, {"clip_filename": "b.mp4"}],
    }), encoding="utf-8")
    for name in ("a.mp4", "b.mp4"):
        (job_dir / name).write_bytes(b"x")

    monkeypatch.setattr(create_postprocess, "find_job_metadata_path", lambda *_: str(metadata_path))
    monkeypatch.setattr(
        create_postprocess,
        "resolve_clip",
        lambda _job, index, _root: SimpleNamespace(
            clip_path=str(job_dir / ("a.mp4" if index == 0 else "b.mp4")),
            job_dir=str(job_dir),
            metadata={"shorts": []},
            clip_info={"clip_filename": "a.mp4" if index == 0 else "b.mp4"},
        ),
    )

    async def fake_compose(**kwargs):
        if kwargs["clip_index"] == 0:
            raise RuntimeError("subtitle burn failed")
        return "captioned_1.mp4"

    monkeypatch.setattr(create_postprocess, "compose_layers", fake_compose)

    summary = asyncio.run(create_postprocess.apply_create_subtitles(
        job_id=job_id, output_root=str(tmp_path), subtitle_params={"mode": "classic"},
    ))
    assert summary["applied"] == 1
    assert summary["failed"] == 1
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert "create_composed_filename" not in saved["shorts"][0]
    assert saved["shorts"][1]["create_composed_filename"] == "captioned_1.mp4"
