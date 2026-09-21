from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import clippyme.api.app as app_module
import clippyme.api.queue_routes as queue_routes
from clippyme.api.schemas import PublishRequest
from clippyme.integrations.social_publisher import ZernioError

ORIGIN = {"Origin": "http://localhost:5175"}


def _future(hour_offset, post_id, show=None):
    stamp = datetime.now(ZoneInfo("Europe/Rome")).replace(minute=0, second=0, microsecond=0) + timedelta(hours=hour_offset)
    metadata = {"clippyme": {"show_id": show}} if show else {}
    return {
        "_id": post_id, "title": post_id, "status": "scheduled",
        "scheduledFor": stamp.isoformat(), "metadata": metadata,
        "platforms": [{"platform": "youtube", "accountId": {"_id": "yt"}}],
    }


@pytest.fixture
def queue_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = {
        "posts": [_future(2, "p1"), _future(4, "p2", "melstroy"),
                  _future(6, "p3", "melstroy"), _future(8, "p4", "melstroy")],
        "moves": [],
    }

    class Client:
        def __init__(self, api_key):
            pass

        def list_all_scheduled_posts(self):
            return state["posts"]

        def reschedule_post(self, post_id, scheduled_for, timezone):
            state["moves"].append((post_id, scheduled_for, timezone))
            if state.get("fail_on_call") == len(state["moves"]):
                raise ZernioError("injected failure")
            return {"_id": post_id, "status": "scheduled", "scheduledFor": scheduled_for}

    monkeypatch.setattr(queue_routes, "ZernioClient", Client)
    monkeypatch.setattr(queue_routes, "load_zernio_config", lambda: {
        "api_key": "key", "timezone": "Europe/Rome", "accounts": {"youtube": "yt"},
    })
    return TestClient(app_module.app, headers=ORIGIN), state


def test_unknown_post_is_immutable_until_mapping_is_saved(queue_client):
    client, _ = queue_client
    before = client.post("/api/publish/queue/posts", json={"account_ids": ["yt"]})
    assert before.status_code == 200
    assert before.json()["posts"][0]["show_id"] is None

    mapped = client.post("/api/publish/queue/map-show", json={
        "account_ids": ["yt"], "post_id": "p1", "show_id": "prince",
    })
    assert mapped.status_code == 200
    after = client.post("/api/publish/queue/posts", json={"account_ids": ["yt"]})
    assert after.json()["posts"][0]["show_id"] == "prince"


def test_plan_rotates_new_show_after_locked_horizon(queue_client):
    client, _ = queue_client
    client.post("/api/publish/queue/map-show", json={
        "account_ids": ["yt"], "post_id": "p1", "show_id": "melstroy",
    })
    response = client.post("/api/publish/queue/plan", json={
        "account_ids": ["yt"], "incoming": [
            {"id": "job:0", "show_id": "prince"}, {"id": "job:1", "show_id": "prince"},
        ],
    })
    assert response.status_code == 200
    assignments = response.json()["assignments"]
    assert assignments[0]["id"] == "job:0"
    assert [row["show_id"] for row in assignments[:4]] == ["prince", "melstroy", "prince", "melstroy"]


def test_apply_parks_all_movers_before_assigning_targets(queue_client):
    client, state = queue_client
    original3 = state["posts"][2]["scheduledFor"]
    original4 = state["posts"][3]["scheduledFor"]
    tz = ZoneInfo("Europe/Rome")
    base = datetime.now(tz) + timedelta(days=2)
    grid = ((10, 30), (12, 30)) if base.weekday() >= 5 else ((8, 30), (11, 30))
    target3 = base.replace(hour=grid[0][0], minute=grid[0][1], second=0, microsecond=0).isoformat()
    target4 = base.replace(hour=grid[1][0], minute=grid[1][1], second=0, microsecond=0).isoformat()
    response = client.post("/api/publish/queue/apply", json={
        "account_ids": ["yt"], "moves": [
            {"post_id": "p3", "expected_scheduled_for": original3, "scheduled_for": target3},
            {"post_id": "p4", "expected_scheduled_for": original4, "scheduled_for": target4},
        ],
    })
    assert response.status_code == 200
    assert response.json() == {"moved": 2}
    assert [move[0] for move in state["moves"]] == ["p3", "p4", "p3", "p4"]
    assert state["moves"][2][1] == target3
    assert state["moves"][3][1] == target4


def test_apply_restages_then_restores_originals_after_partial_target_failure(queue_client):
    client, state = queue_client
    original3 = state["posts"][2]["scheduledFor"]
    original4 = state["posts"][3]["scheduledFor"]
    base = datetime.now(ZoneInfo("Europe/Rome")) + timedelta(days=2)
    grid = ((10, 30), (12, 30)) if base.weekday() >= 5 else ((8, 30), (11, 30))
    state["fail_on_call"] = 3
    response = client.post("/api/publish/queue/apply", json={
        "account_ids": ["yt"], "moves": [
            {"post_id": "p3", "expected_scheduled_for": original3,
             "scheduled_for": base.replace(hour=grid[0][0], minute=grid[0][1], second=0, microsecond=0).isoformat()},
            {"post_id": "p4", "expected_scheduled_for": original4,
             "scheduled_for": base.replace(hour=grid[1][0], minute=grid[1][1], second=0, microsecond=0).isoformat()},
        ],
    })
    assert response.status_code == 502
    assert [(post, stamp) for post, stamp, _ in state["moves"][-2:]] == [
        ("p3", datetime.fromisoformat(original3).isoformat()),
        ("p4", datetime.fromisoformat(original4).isoformat()),
    ]



def test_plan_dedupes_legacy_job_id_by_show_and_clip_index(queue_client):
    client, state = queue_client
    state["posts"] = [{
        "_id": "legacy-post",
        "title": "legacy",
        "status": "scheduled",
        "scheduledFor": (
            datetime.now(ZoneInfo("Europe/Rome")) + timedelta(hours=4)
        ).isoformat(),
        "metadata": {"clippyme": {
            "show_id": "source-episode123",
            "queue_item_id": "old-job-uuid:7",
        }},
        "platforms": [{"platform": "youtube", "accountId": {"_id": "yt"}}],
    }]
    response = client.post("/api/publish/queue/plan", json={
        "account_ids": ["yt"],
        "incoming": [{
            "id": "source-episode123:7",
            "show_id": "source-episode123",
        }],
    })
    assert response.status_code == 200
    assert response.json()["duplicates"] == ["source-episode123:7"]
    assert response.json()["assignments"] == []


def test_apply_protects_nearest_real_instants_across_mixed_offsets(queue_client):
    client, state = queue_client
    day = (datetime.now(ZoneInfo("UTC")) + timedelta(days=2)).date().isoformat()
    state["posts"] = [
        {
            "_id": "p1", "title": "p1", "status": "scheduled",
            "scheduledFor": f"{day}T08:30:00+03:00",
            "metadata": {"clippyme": {"show_id": "show"}},
            "platforms": [{"platform": "youtube", "accountId": {"_id": "yt"}}],
        },
        {
            "_id": "p2", "title": "p2", "status": "scheduled",
            "scheduledFor": f"{day}T08:00:00+02:00",
            "metadata": {"clippyme": {"show_id": "show"}},
            "platforms": [{"platform": "youtube", "accountId": {"_id": "yt"}}],
        },
        {
            "_id": "p3", "title": "p3", "status": "scheduled",
            "scheduledFor": f"{day}T08:15:00+02:00",
            "metadata": {"clippyme": {"show_id": "show"}},
            "platforms": [{"platform": "youtube", "accountId": {"_id": "yt"}}],
        },
    ]
    target_day = datetime.now(ZoneInfo("Europe/Rome")) + timedelta(days=4)
    grid = ((10, 30), (12, 30)) if target_day.weekday() >= 5 else ((18, 0), (20, 0))
    target = target_day.replace(
        hour=grid[0][0], minute=grid[0][1], second=0, microsecond=0
    ).isoformat()
    response = client.post("/api/publish/queue/apply", json={
        "account_ids": ["yt"],
        "moves": [{
            "post_id": "p1",
            "expected_scheduled_for": state["posts"][0]["scheduledFor"],
            "scheduled_for": target,
        }],
    })
    assert response.status_code == 409
    assert "protected horizon" in response.json()["detail"]
    assert state["moves"] == []


def test_publish_request_accepts_unicode_episode_queue_id():
    req = PublishRequest(
        platforms=[{"platform": "youtube", "accountId": "yt"}],
        queue_show_id="Вписка-Мелстрой",
        queue_item_id="вписка-мелстрой:3",
    )
    assert req.queue_show_id == "вписка-мелстрой"
    assert req.queue_item_id == "вписка-мелстрой:3"
