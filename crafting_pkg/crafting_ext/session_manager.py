from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Dict, List, Optional

from cachetools import TTLCache

log = logging.getLogger("ballsdex.packages.crafting.session")


class CraftingSessionData:
    """Session data for a single user's crafting session."""

    __slots__ = ("player", "ingredient_instances", "created_at", "last_accessed", "access_count", "_lock", "message")

    def __init__(self, player, ingredient_instances: List[int]):
        self.player = player
        self.ingredient_instances = ingredient_instances.copy()
        self.created_at = datetime.datetime.now()
        self.last_accessed = datetime.datetime.now()
        self.access_count = 0
        self._lock = asyncio.Lock()
        self.message = None

    def to_dict(self) -> Dict:
        return {
            "player": self.player,
            "ingredient_instances": self.ingredient_instances.copy(),
            "message": self.message,
        }

    def is_valid(self) -> tuple[bool, str]:
        if datetime.datetime.now() - self.created_at > datetime.timedelta(minutes=30):
            return False, "Session expired"
        if not isinstance(self.ingredient_instances, list):
            return False, "Ingredient instances corrupted"
        return True, "Valid"

    def remove_ingredients(self, instance_ids: List[int]) -> bool:
        try:
            removed = 0
            for iid in instance_ids:
                if iid in self.ingredient_instances:
                    self.ingredient_instances.remove(iid)
                    removed += 1
            log.debug(
                "[SESSION %s] INGREDIENTS_REMOVED: Removed %d, %d remaining",
                self.player.id,
                removed,
                len(self.ingredient_instances),
            )
            return True
        except Exception as e:
            log.exception("[SESSION %s] INGREDIENT_REMOVAL_FAILED: %s", self.player.id, e)
            return False


# Use TTLCache with 30 min TTL and max 10000 sessions
# This automatically evicts expired entries on access
_session_storage: TTLCache[int, CraftingSessionData] = TTLCache(maxsize=10000, ttl=1800)

# Background cleanup task reference
_cleanup_task: Optional[asyncio.Task] = None


def create_session(user_id: int, player, ingredient_instances: List[int]) -> bool:
    try:
        if user_id in _session_storage:
            log.warning("Replacing existing crafting session for user %d", user_id)
        session = CraftingSessionData(player, ingredient_instances)
        _session_storage[user_id] = session
        log.debug("[SESSION %s] SESSION_CREATED: Initial ingredients: %d", player.id, len(ingredient_instances))
        return True
    except Exception as e:
        log.exception("[SESSION_MANAGER] Failed to create session for user %s: %s", user_id, e)
        return False


def get_session(user_id: int) -> Optional[CraftingSessionData]:
    session = _session_storage.get(user_id)
    if session is None:
        return None
    is_valid, reason = session.is_valid()
    if not is_valid:
        # TTLCache should have evicted, but double-check
        _session_storage.pop(user_id, None)
        return None
    return session


def end_session(user_id: int, reason: str = "Manual") -> bool:
    if user_id not in _session_storage:
        return False
    log.debug("[SESSION %s] SESSION_ENDED: Reason: %s", user_id, reason)
    _session_storage.pop(user_id, None)
    return True


def session_exists(user_id: int) -> bool:
    session = _session_storage.get(user_id)
    if session is None:
        return False
    is_valid, _ = session.is_valid()
    if not is_valid:
        _session_storage.pop(user_id, None)
        return False
    return True


async def cleanup_expired_sessions() -> int:
    """Manual cleanup - mostly for metrics since TTLCache handles expiration."""
    before = len(_session_storage)
    # Access all items to trigger TTL eviction
    for uid in list(_session_storage.keys()):
        _ = _session_storage.get(uid)
    after = len(_session_storage)
    cleaned = before - after
    if cleaned:
        log.info("Crafting session cleanup: removed %d expired sessions", cleaned)
    return cleaned


async def start_cleanup_task(interval: int = 300) -> asyncio.Task:
    """Start background cleanup task."""
    async def _cleanup_loop():
        while True:
            await asyncio.sleep(interval)
            try:
                await cleanup_expired_sessions()
            except Exception:
                log.exception("Error in crafting session cleanup task")

    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_cleanup_loop())
        log.info("Crafting session cleanup task started (interval=%ds)", interval)
    return _cleanup_task


async def stop_cleanup_task() -> None:
    """Stop background cleanup task."""
    global _cleanup_task
    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
        log.info("Crafting session cleanup task stopped")


# Export for backward compatibility
crafting_sessions = _session_storage
