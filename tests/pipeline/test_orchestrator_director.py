"""Regression coverage for Director mode in the checkpointed orchestrator."""
from clippyme.pipeline.orchestrator import _parse_args


def test_orchestrator_cli_accepts_director_mode():
    args = _parse_args([
        "-i", "input.mp4",
        "-o", "output",
        "--reframe-mode", "director",
    ])
    assert args.reframe_mode == "director"



def test_empty_analysis_does_not_call_removed_texttiling(tmp_path):
    from types import SimpleNamespace
    from clippyme.pipeline.orchestrator import _load_or_analyze

    class _State:
        def artifact(self, _name):
            return None

        def completed(self, _stage):
            return False

        def start(self, *_args, **_kwargs):
            pass

        def set_clip_total(self, _total):
            pass

        def complete_stage(self, *_args, **_kwargs):
            pass

    class _Legacy:
        @staticmethod
        def get_viral_clips(_transcript, _duration, instructions=None):
            return None

    args = SimpleNamespace(
        skip_analysis=False,
        instructions=None,
        monitor=False,
        aspect="9:16",
        reframe_mode="auto",
    )
    transcript = {"text": "", "segments": []}

    clips, metadata = _load_or_analyze(
        args,
        str(tmp_path / "input.mp4"),
        "video",
        str(tmp_path),
        247.0,
        transcript,
        _State(),
        _Legacy(),
    )

    assert clips["shorts"] == []
    assert clips["transcript"] == transcript
    assert metadata.endswith("video_metadata.json")
