"""On-demand fixed-slot queue operations; no background scheduler."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from clippyme.api.security import enforce_rate_limit, require_trusted_config_request
from clippyme.domain.fixed_queue import WEEKDAY_SLOTS, WEEKEND_SLOTS, plan_fixed_queue
from clippyme.integrations.social_publisher import ZernioClient, ZernioError
from clippyme.storage.config_store import load_zernio_config

router = APIRouter(prefix="/api/publish/queue")
_mapping_lock = threading.RLock()
_apply_lock = threading.RLock()
_mapping_path = os.path.join("data", "zernio_show_map.json")


def _read_mapping() -> dict[str, str]:
    try:
        with open(_mapping_path, encoding="utf-8") as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _write_mapping(data: dict[str, str]) -> None:
    folder = os.path.dirname(_mapping_path)
    os.makedirs(folder, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".show-map-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, _mapping_path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _client_and_config():
    config = load_zernio_config()
    if not config.get("api_key"):
        raise HTTPException(400, "Zernio API key not configured")
    return ZernioClient(config["api_key"]), config


def _post_accounts(post: dict) -> set[str]:
    result = set()
    for target in post.get("platforms", []):
        if not isinstance(target, dict):
            continue
        account = target.get("accountId")
        value = account.get("_id") if isinstance(account, dict) else account
        if value:
            result.add(str(value))
    return result


def _selected_ids(account_ids: list[str], config: dict) -> set[str]:
    selected = set(account_ids)
    configured = set((config.get("accounts") or {}).values())
    if not selected or not selected.issubset(configured):
        raise HTTPException(400, "Unknown Zernio account ID")
    return selected


def _snapshot(client: ZernioClient, selected: set[str]) -> list[dict]:
    try:
        posts = client.list_all_scheduled_posts()
    except ZernioError as exc:
        raise HTTPException(502, f"Could not read Zernio schedule: {exc}") from exc
    mapping = _read_mapping()
    result = []
    for post in posts:
        accounts = _post_accounts(post)
        if not accounts.intersection(selected):
            continue
        metadata = (post.get("metadata") or {}).get("clippyme") or {}
        post_id = str(post.get("_id") or post.get("id") or "")
        scheduled_for = post.get("scheduledFor")
        if not post_id or not scheduled_for:
            continue
        result.append({
            "id": post_id,
            "title": str(post.get("title") or post.get("content") or "")[:160],
            "scheduled_for": scheduled_for,
            "show_id": (mapping.get(post_id) or metadata.get("show_id") or "").strip().casefold() or None,
            "queue_item_id": metadata.get("queue_item_id"),
            "movable": bool(accounts) and accounts.issubset(selected),
        })
    return sorted(result, key=lambda item: item["scheduled_for"])


class Scope(BaseModel):
    account_ids: list[str] = Field(min_length=1, max_length=14)


class ShowMapping(Scope):
    post_id: str = Field(min_length=1, max_length=80)
    show_id: str = Field(min_length=1, max_length=64)


class Incoming(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    show_id: str = Field(min_length=1, max_length=64)


class PlanRequest(Scope):
    incoming: list[Incoming] = Field(max_length=100)


class Move(BaseModel):
    post_id: str = Field(min_length=1, max_length=80)
    expected_scheduled_for: str = Field(max_length=64)
    scheduled_for: str = Field(max_length=64)


class ApplyRequest(Scope):
    moves: list[Move] = Field(max_length=100)


@router.post("/posts")
def list_queue_posts(body: Scope, request: Request):
    require_trusted_config_request(request)
    client, config = _client_and_config()
    selected = _selected_ids(body.account_ids, config)
    return {"posts": _snapshot(client, selected),
            "timezone": config.get("timezone") or "Europe/Rome", "lock_depth": 2}


@router.post("/map-show")
def map_queue_show(body: ShowMapping, request: Request):
    require_trusted_config_request(request)
    enforce_rate_limit(request, "publish", capacity=30, refill_per_sec=30 / 60)
    client, config = _client_and_config()
    selected = _selected_ids(body.account_ids, config)
    posts = _snapshot(client, selected)
    if not any(post["id"] == body.post_id and post["movable"] for post in posts):
        raise HTTPException(404, "Scheduled post not found in the selected accounts")
    with _mapping_lock:
        mapping = _read_mapping()
        show_id = body.show_id.strip().casefold()
        if not show_id:
            raise HTTPException(400, "show_id cannot be blank")
        mapping[body.post_id] = show_id
        _write_mapping(mapping)
    return {"post_id": body.post_id, "show_id": show_id}


@router.post("/plan")
def plan_queue(body: PlanRequest, request: Request):
    require_trusted_config_request(request)
    client, config = _client_and_config()
    selected = _selected_ids(body.account_ids, config)
    posts = _snapshot(client, selected)
    known_items = {post["queue_item_id"] for post in posts if post.get("queue_item_id")}
    incoming = [{**item.model_dump(), "show_id": item.show_id.strip().casefold()}
                for item in body.incoming if item.id not in known_items]
    timezone = config.get("timezone") or "Europe/Rome"
    try:
        assignments = plan_fixed_queue(posts, incoming, now=datetime.now(ZoneInfo(timezone)),
                                       timezone=timezone, lock_depth=2)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"assignments": assignments, "posts": posts, "timezone": timezone,
            "duplicates": sorted(known_items.intersection(item.id for item in body.incoming))}


def _parse_timestamp(value: str, timezone: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ZoneInfo(timezone))
    return stamp.astimezone(ZoneInfo(timezone))


@router.post("/apply")
def apply_queue_moves(body: ApplyRequest, request: Request):
    """Move existing posts as a batch, with temporary parking and rollback."""
    require_trusted_config_request(request)
    enforce_rate_limit(request, "publish", capacity=30, refill_per_sec=30 / 60)
    if not body.moves:
        return {"moved": 0}
    with _apply_lock:
        return _apply_queue_moves_locked(body)


def _apply_queue_moves_locked(body: ApplyRequest):
    client, config = _client_and_config()
    selected = _selected_ids(body.account_ids, config)
    timezone = config.get("timezone") or "Europe/Rome"
    now = datetime.now(ZoneInfo(timezone))
    posts = _snapshot(client, selected)
    by_id = {post["id"]: post for post in posts}
    future = [post for post in posts if _parse_timestamp(post["scheduled_for"], timezone) > now]
    protected_ids = {post["id"] for post in future[:2]}
    targets = set()
    originals = {}
    for move in body.moves:
        post = by_id.get(move.post_id)
        if not post or not post.get("show_id") or not post["movable"]:
            raise HTTPException(409, "Queue changed or contains an unmapped post")
        current = _parse_timestamp(post["scheduled_for"], timezone)
        expected = _parse_timestamp(move.expected_scheduled_for, timezone)
        target = _parse_timestamp(move.scheduled_for, timezone)
        grid = WEEKEND_SLOTS if target.weekday() >= 5 else WEEKDAY_SLOTS
        if current != expected:
            raise HTTPException(409, "Zernio schedule changed; refresh the plan")
        if post["id"] in protected_ids or current <= now + timedelta(minutes=30):
            raise HTTPException(409, "Post is inside the protected horizon")
        if target <= now + timedelta(minutes=30) or (target.hour, target.minute) not in grid:
            raise HTTPException(400, "Target is not a future fixed slot")
        if target.isoformat() in targets:
            raise HTTPException(400, "Two posts cannot use the same target slot")
        targets.add(target.isoformat())
        originals[post["id"]] = current

    moved_ids = set(originals)
    occupied_by_fixed = {_parse_timestamp(post["scheduled_for"], timezone).isoformat()
                         for post in posts if post["id"] not in moved_ids}
    if targets.intersection(occupied_by_fixed):
        raise HTTPException(409, "A target slot is occupied by a locked or external post")

    # All movers are parked first, otherwise swapping two occupied slots would
    # trigger Zernio's queue-slot conflict. Roll back best-effort on any error.
    farthest = max(_parse_timestamp(move.scheduled_for, timezone) for move in body.moves)
    parked = []
    assigning_targets = False
    try:
        for index, move in enumerate(body.moves):
            parking = (farthest + timedelta(days=35, hours=5, minutes=index * 7)).replace(second=0, microsecond=0)
            client.reschedule_post(move.post_id, parking.isoformat(), timezone)
            parked.append(move.post_id)
        assigning_targets = True
        for move in body.moves:
            updated = client.reschedule_post(move.post_id, _parse_timestamp(move.scheduled_for, timezone).isoformat(), timezone)
            if updated.get("status") != "scheduled":
                raise ZernioError("Zernio did not confirm scheduled status")
    except (ZernioError, ValueError) as exc:
        # If target assignment had started, a target can be another post's
        # original slot. Re-park every mover before restoring originals.
        if assigning_targets:
            rollback_base = farthest + timedelta(days=70, hours=4)
            for index, post_id in enumerate(parked):
                try:
                    client.reschedule_post(post_id, (rollback_base + timedelta(minutes=index * 7)).isoformat(), timezone)
                except ZernioError:
                    pass
        for post_id in sorted(parked, key=lambda value: originals[value]):
            try:
                client.reschedule_post(post_id, originals[post_id].isoformat(), timezone)
            except ZernioError:
                pass
        raise HTTPException(502, f"Could not apply queue safely: {exc}") from exc
    return {"moved": len(body.moves)}
