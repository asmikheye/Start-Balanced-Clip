"""Regression tests for the Director reframe API contract."""
import asyncio
import importlib

from clippyme.api.schemas import ReframeRequest


def test_reframe_endpoint_accepts_director(monkeypatch):
    app_module = importlib.import_module("clippyme.api.app")

    monkeypatch.setattr(app_module, "require_trusted_config_request", lambda request: None)
    monkeypatch.setattr(
        app_module,
        "enforce_rate_limit",
        lambda request, key, capacity, refill_per_sec: None,
    )
    monkeypatch.setattr(app_module, "is_valid_job_id", lambda job_id: True)

    async def fake_run_reframe(**kwargs):
        return kwargs

    monkeypatch.setattr(app_module, "run_reframe", fake_run_reframe)

    result = asyncio.run(
        app_module.reframe_clip(
            "job-test",
            0,
            ReframeRequest(reframe_mode="director"),
            object(),
        )
    )
    assert result["mode"] == "director"
