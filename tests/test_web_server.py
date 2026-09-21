import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from web_server import MAX_REQUEST_BYTES, WEB_DIR, json_bytes


class WebServerTests(unittest.TestCase):
    def test_web_assets_exist(self):
        for name in ("index.html", "styles.css", "app.js"):
            self.assertTrue((WEB_DIR / name).is_file(), name)

    def test_web_discloses_validated_llm_boundary(self):
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        script = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("LLM 受控只读分析", html)
        self.assertIn("销售明细和查询结果留在本机", html)
        self.assertIn("参数已校验", script)

    def test_opening_html_file_redirects_to_local_server(self):
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn('window.location.protocol === "file:"', html)
        self.assertIn('window.location.replace("http://127.0.0.1:8765/")', html)

    def test_browser_sends_conversation_id_and_clear_resets_it(self):
        script = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("conversationId", script)
        self.assertIn("createConversationId()", script)
        self.assertIn("已使用上一轮上下文", script)

    def test_json_response_preserves_chinese(self):
        payload = json.loads(json_bytes({"answer": "本周销售额"}).decode("utf-8"))
        self.assertEqual(payload["answer"], "本周销售额")

    def test_request_limit_is_bounded(self):
        self.assertLessEqual(MAX_REQUEST_BYTES, 16_384)


if __name__ == "__main__":
    unittest.main()
