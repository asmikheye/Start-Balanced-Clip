from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from clippyme.domain.fixed_queue import plan_fixed_queue

TZ = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 9, 21, 7, 0, tzinfo=TZ)


def items(show, count):
    return [{"id": f"{show}{number}", "show_id": show} for number in range(1, count + 1)]


def test_one_show_fills_same_day_grid_in_order():
    plan = plan_fixed_queue([], items("M", 7), now=NOW, timezone="Europe/Rome")
    assert [row["id"] for row in plan] == [f"M{i}" for i in range(1, 8)]
    assert [datetime.fromisoformat(row["scheduled_for"]).strftime("%H:%M") for row in plan] == [
        "08:30", "11:30", "13:30", "16:00", "18:00", "20:00", "22:00"]


def test_shows_rotate_and_keep_internal_order():
    plan = plan_fixed_queue([], items("M", 5) + items("P", 3), now=NOW, timezone="Europe/Rome")
    assert [row["id"] for row in plan] == ["M1", "P1", "M2", "P2", "M3", "P3", "M4", "M5"]


def test_two_nearest_and_unknown_posts_stay_locked():
    existing = [{"id": f"M{i}", "show_id": "M", "scheduled_for": f"2026-09-21T{hour:02d}:00+02:00"}
                for i, hour in enumerate((9, 11, 13, 16, 18), 1)]
    existing.append({"id": "foreign", "scheduled_for": "2026-09-21T20:00+02:00"})
    plan = plan_fixed_queue(existing, items("P", 2), now=NOW, timezone="Europe/Rome")
    assert [row["id"] for row in plan] == ["P1", "M3", "P2", "M4", "M5"]
    assert all(row["id"] not in {"M1", "M2", "foreign"} for row in plan)


def test_insufficient_horizon_fails_before_writes():
    with pytest.raises(ValueError, match="not enough fixed slots"):
        plan_fixed_queue([], items("M", 8), now=NOW, timezone="Europe/Rome", days=1)



def test_fresh_show_gets_first_movable_slot_with_multiple_old_shows():
    existing = [
        {"id": "A1", "show_id": "A", "scheduled_for": "2026-09-21T08:30:00+02:00"},
        {"id": "A2", "show_id": "A", "scheduled_for": "2026-09-21T11:30:00+02:00"},
        {"id": "B1", "show_id": "B", "scheduled_for": "2026-09-21T13:30:00+02:00"},
        {"id": "A3", "show_id": "A", "scheduled_for": "2026-09-21T16:00:00+02:00"},
        {"id": "B2", "show_id": "B", "scheduled_for": "2026-09-21T18:00:00+02:00"},
    ]
    plan = plan_fixed_queue(
        existing,
        items("FRESH", 2),
        now=NOW,
        timezone="Europe/Rome",
        lock_depth=2,
    )
    assert plan[0]["id"] == "FRESH1"
    assert plan[0]["show_id"] == "FRESH"
    assert [row["id"] for row in plan if row["show_id"] == "FRESH"] == [
        "FRESH1", "FRESH2",
    ]
