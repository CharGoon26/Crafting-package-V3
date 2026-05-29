from __future__ import annotations

import datetime
import threading
import traceback
from typing import Dict, List, Optional


class CraftingSessionData:
    def __init__(self, player, ingredient_instances: List[int]):
        self.player = player
        self.ingredient_instances = ingredient_instances.copy()
        self.created_at = datetime.datetime.now()
        self.last_accessed = datetime.datetime.now()
        self.access_count = 0
        self.debug_log = []
        self._lock = threading.Lock()

    def log_access(self, operation: str, details: str = ""):
        with self._lock:
            self.last_accessed = datetime.datetime.now()
            self.access_count += 1
            stack = traceback.extract_stack()
            caller_info = f"{stack[-3].filename}:{stack[-3].lineno}" if len(stack) >= 3 else "unknown"
            self.debug_log.append({
                "timestamp": datetime.datetime.now().isoformat(),
                "operation": operation,
                "details": details,
                "caller": caller_info,
                "ingredient_count": len(self.ingredient_instances),
                "access_count": self.access_count,
            })
            print(
                f"[SESSION {self.player.id}] {operation}: {details} "
                f"(ingredients: {len(self.ingredient_instances)}) from {caller_info}"
            )

    def to_dict(self) -> Dict:
        self.log_access("CONVERTED_TO_DICT", "Session data accessed")
        return {
            "player": self.player,
            "ingredient_instances": self.ingredient_instances.copy(),
        }

    def is_valid(self) -> tuple[bool, str]:
        if datetime.datetime.now() - self.created_at > datetime.timedelta(minutes=30):
            return False, "Session expired"
        if not isinstance(self.ingredient_instances, list):
            return False, "Ingredient instances corrupted"
        return True, "Valid"

    def remove_ingredients(self, instance_ids: List[int]) -> bool:
        with self._lock:
            try:
                removed = 0
                for iid in instance_ids:
                    if iid in self.ingredient_instances:
                        self.ingredient_instances.remove(iid)
                        removed += 1
                self.log_access("INGREDIENTS_REMOVED", f"Removed {removed}, {len(self.ingredient_instances)} remaining")
                return True
            except Exception as e:
                self.log_access("INGREDIENT_REMOVAL_FAILED", f"Error: {e}")
                return False

    def print_debug_log(self):
        print(f"[SESSION {self.player.id}] DEBUG LOG:")
        for entry in self.debug_log:
            print(f"  {entry['timestamp']}: {entry['operation']} - {entry['details']} (from {entry['caller']})")


_session_storage: Dict[int, CraftingSessionData] = {}


def create_session(user_id: int, player, ingredient_instances: List[int]) -> bool:
    try:
        if user_id in _session_storage:
            _session_storage[user_id].log_access("SESSION_REPLACED", "Creating new session")
        session = CraftingSessionData(player, ingredient_instances)
        session.log_access("SESSION_CREATED", f"Initial ingredients: {len(ingredient_instances)}")
        _session_storage[user_id] = session
        return True
    except Exception as e:
        print(f"[SESSION_MANAGER] Failed to create session for user {user_id}: {e}")
        return False


def get_session(user_id: int) -> Optional[Dict]:
    if user_id not in _session_storage:
        return None
    session = _session_storage[user_id]
    is_valid, reason = session.is_valid()
    if not is_valid:
        del _session_storage[user_id]
        return None
    session.log_access("SESSION_ACCESSED", "Session data retrieved")
    return session.to_dict()


def end_session(user_id: int, reason: str = "Manual") -> bool:
    if user_id not in _session_storage:
        return False
    _session_storage[user_id].log_access("SESSION_ENDED", f"Reason: {reason}")
    del _session_storage[user_id]
    return True


def session_exists(user_id: int) -> bool:
    if user_id not in _session_storage:
        return False
    is_valid, _ = _session_storage[user_id].is_valid()
    if not is_valid:
        del _session_storage[user_id]
        return False
    return True


def cleanup_expired_sessions() -> int:
    expired = [uid for uid, s in _session_storage.items() if not s.is_valid()[0]]
    for uid in expired:
        del _session_storage[uid]
    return len(expired)


crafting_sessions = _session_storage
