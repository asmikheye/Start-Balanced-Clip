"""Pure fixed-slot planning for the on-demand Zernio queue."""
from __future__ import annotations

from collections import deque
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAY_SLOTS = ((8, 30), (11, 30), (13, 30), (16, 0), (18, 0), (20, 0), (22, 0))
WEEKEND_SLOTS = ((10, 30), (12, 30), (14, 30), (16, 30), (18, 30), (20, 30), (22, 30))


def fixed_slots(after: datetime, *, timezone: str, days: int = 30):
    tz = ZoneInfo(timezone)
    after = after.astimezone(tz)
    for offset in range(days):
        day = after.date() + timedelta(days=offset)
        grid = WEEKEND_SLOTS if day.weekday() >= 5 else WEEKDAY_SLOTS
        for hour, minute in grid:
            slot = datetime.combine(day, time(hour, minute), tzinfo=tz)
            if slot > after:
                yield slot


def plan_fixed_queue(existing: list[dict], incoming: list[dict], *, now: datetime,
                     timezone: str, lock_depth: int = 2,
                     min_lead_minutes: int = 30, days: int = 30) -> list[dict]:
    """Assign movable posts to fixed slots with round-robin show rotation.

    Unknown shows, posts shared with unselected accounts, the closest
    ``lock_depth`` posts, and posts inside the lead window remain untouched.
    Input order is preserved inside each show.
    """
    if lock_depth < 0:
        raise ValueError("lock_depth must be nonnegative")
    tz = ZoneInfo(timezone)
    now = now.astimezone(tz)
    parsed = []
    for item in existing:
        stamp = datetime.fromisoformat(item["scheduled_for"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=tz)
        parsed.append((stamp.astimezone(tz), item))
    parsed = sorted((pair for pair in parsed if pair[0] > now), key=lambda pair: (pair[0], str(pair[1]["id"])))

    lead = now + timedelta(minutes=min_lead_minutes)
    locked, protected, movable = [], [], []
    for position, (stamp, item) in enumerate(parsed):
        must_lock = (position < lock_depth or stamp <= lead or not item.get("show_id")
                     or item.get("movable") is False)
        if must_lock:
            locked.append((stamp, item))
            if position < lock_depth or stamp <= lead:
                protected.append((stamp, item))
        else:
            movable.append(item)

    incoming_ids = {id(item) for item in incoming}
    groups: dict[str, deque] = {}
    incoming_show_order: list[str] = []
    existing_show_order: list[str] = []

    # Keep clip order inside each show, but seed the round-robin with fresh
    # incoming shows so a newly processed episode receives the nearest
    # available movable slot.
    for item, is_incoming in (
        [(item, False) for item in movable] + [(item, True) for item in incoming]
    ):
        show = str(item.get("show_id") or "").strip()
        if not show:
            raise ValueError("every queue item requires show_id")
        groups.setdefault(show, deque()).append(item)
        order = incoming_show_order if is_incoming else existing_show_order
        if show not in order:
            order.append(show)
    if not groups:
        return []

    shows = deque(incoming_show_order + [
        show for show in existing_show_order if show not in incoming_show_order
    ])
    last_show = protected[-1][1].get("show_id") if protected else None
    start_after = max((stamp for stamp, _ in protected), default=lead)
    blocked = [stamp for stamp, _ in locked]
    available = (slot for slot in fixed_slots(start_after, timezone=timezone, days=days)
                 if all(abs((slot - occupied).total_seconds()) >= 30 * 60 for occupied in blocked))
    result = []
    while shows:
        choices = [show for show in shows if groups[show]]
        if not choices:
            break
        show = next((candidate for candidate in choices if candidate != last_show), choices[0])
        try:
            slot = next(available)
        except StopIteration as exc:
            raise ValueError("not enough fixed slots within the scheduling horizon") from exc
        item = groups[show].popleft()
        result.append({"id": item["id"], "show_id": show, "scheduled_for": slot.isoformat(),
                       "new": id(item) in incoming_ids})
        shows.remove(show)
        if groups[show]:
            shows.append(show)
        last_show = show
    return result
