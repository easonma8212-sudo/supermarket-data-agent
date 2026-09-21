import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from conversation_store import ConversationStore


class ConversationStoreTests(unittest.TestCase):
    def test_state_is_copied_on_put_and_get(self):
        store = ConversationStore()
        state = {"last_plan": {"tool": "query_metrics"}}
        store.put("conversation-1", state)
        state["last_plan"]["tool"] = "changed"
        loaded = store.get("conversation-1")
        self.assertEqual(loaded["last_plan"]["tool"], "query_metrics")
        loaded["last_plan"]["tool"] = "also-changed"
        self.assertEqual(
            store.get("conversation-1")["last_plan"]["tool"], "query_metrics"
        )

    @patch("conversation_store.time.monotonic", side_effect=[0, 2, 2])
    def test_expired_state_is_removed(self, _):
        store = ConversationStore(ttl_seconds=1)
        store.put("conversation-1", {"value": 1})
        self.assertIsNone(store.get("conversation-1"))


if __name__ == "__main__":
    unittest.main()
