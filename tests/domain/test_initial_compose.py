import asyncio
import json

from clippyme.domain.job_runner import apply_initial_compose


JOB = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def test_initial_compose_burns_create_recipe_and_persists_preview(tmp_path):
    job_dir = tmp_path / JOB
    job_dir.mkdir()
    (job_dir / "clip_title_clip_1.mp4").write_bytes(b"raw-video")
    metadata_path = job_dir / "video_metadata.json"
    metadata_path.write_text(json.dumps({
        "shorts": [{
            "clip_filename": "clip_title_clip_1.mp4",
            "video_title_for_youtube_short": "clip title",
            "viral_hook_text": "AI hook",
            "start": 0,
            "end": 20,
        }],
        "transcript": {"segments": []},
    }))

    calls = []

    async def fake_compose(**kwargs):
        calls.append(kwargs)
        (job_dir / "clip title.mp4").write_bytes(b"composed")
        return "clip title.mp4"

    recipe = {
        "toggles": {
            "smartcut": False, "subtitles": True, "hook": True,
            "logo": False, "grade": False, "banner": False,
        },
        "hook_params": {"position": "top", "size": "M"},
        "subtitle_params": {"mode": "karaoke", "preset": "hormozi_bold"},
        "logo_params": {}, "grade_params": {}, "banner_params": {},
        "drop_ranges": [],
    }
    count = asyncio.run(apply_initial_compose(
        JOB, {"compose_recipe": recipe}, str(tmp_path), compose_impl=fake_compose,
    ))
    assert count == 1
    assert calls[0]["hook_params"]["text"] == "AI hook"
    saved = json.loads(metadata_path.read_text())
    clip = saved["shorts"][0]
    assert clip["composed_filename"] == "clip title.mp4"
    assert clip["last_compose"]["toggles"]["subtitles"] is True


def test_initial_compose_no_active_layers_is_noop(tmp_path):
    called = []

    async def fake_compose(**kwargs):
        called.append(kwargs)
        return "unused.mp4"

    count = asyncio.run(apply_initial_compose(
        JOB,
        {"compose_recipe": {"toggles": {
            "smartcut": False, "subtitles": False, "hook": False,
            "logo": False, "grade": False, "banner": False,
        }}},
        str(tmp_path),
        compose_impl=fake_compose,
    ))
    assert count == 0
    assert called == []
