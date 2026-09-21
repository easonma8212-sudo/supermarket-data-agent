"""Small in-memory conversation state store for the local web agent."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
import time
from typing import Any


class ConversationStore:
    def __init__(self, max_sessions: int = 100, ttl_seconds: int = 3600):
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = RLock()

    def _prune(self, now: float) -> None:
        expired = [
            conversation_id
            for conversation_id, (updated_at, _) in self._items.items()
            if now - updated_at > self.ttl_seconds
        ]
        for conversation_id in expired:
            self._items.pop(conversation_id, None)
        while len(self._items) >= self.max_sessions:
            oldest = min(self._items, key=lambda key: self._items[key][0])
            self._items.pop(oldest, None)

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            item = self._items.get(conversation_id)
            if item is None:
                return None
            _, state = item
            self._items[conversation_id] = (now, state)
            return deepcopy(state)

    def put(self, conversation_id: str, state: dict[str, Any] | None) -> None:
        if state is None:
            return
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._items[conversation_id] = (now, deepcopy(state))

    def delete(self, conversation_id: str) -> None:
        with self._lock:
            self._items.pop(conversation_id, None)
