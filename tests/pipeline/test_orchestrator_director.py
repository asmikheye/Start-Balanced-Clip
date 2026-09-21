"""Regression coverage for Director mode in the checkpointed orchestrator."""
from clippyme.pipeline.orchestrator import _parse_args


def test_orchestrator_cli_accepts_director_mode():
    args = _parse_args([
        "-i", "input.mp4",
        "-o", "output",
        "--reframe-mode", "director",
    ])
    assert args.reframe_mode == "director"
