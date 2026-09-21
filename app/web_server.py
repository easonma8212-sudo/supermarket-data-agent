"""Local-only web interface for the supermarket sales agent."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from urllib.parse import unquote, urlparse


APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
WEB_DIR = ROOT / "web"
sys.path.insert(0, str(APP_DIR))

from agent_runtime import AgentRuntimeError, answer_with_ai  # noqa: E402
from ai_planner import load_local_environment  # noqa: E402
from conversation_store import ConversationStore  # noqa: E402
from sales_agent import answer, get_bounds  # noqa: E402


MAX_REQUEST_BYTES = 16_384


def json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class SalesAgentHandler(BaseHTTPRequestHandler):
    database = "supermarket_agent"
    agent_mode = "rules"
    conversations = ConversationStore()

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            self.handle_status()
            return
        self.serve_static(path)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/chat":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
            return
        self.handle_chat()

    def handle_status(self) -> None:
        try:
            bounds = get_bounds(self.database)
            self.send_json(HTTPStatus.OK, {
                "ready": True,
                "earliest": bounds.earliest,
                "latestTimestamp": bounds.latest_timestamp,
                "latestCompleteDate": bounds.latest_complete_date,
                "agentMode": self.agent_mode,
            })
        except Exception:
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "ready": False,
                "error": "暂时无法连接本机经营数据库",
            })

    def handle_chat(self) -> None:
        content_length = self.headers.get("Content-Length")
        try:
            length = int(content_length or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "问题内容为空或过长"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "请求格式无效"})
            return

        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "请输入经营问题"})
            return
        question = question.strip()
        if len(question) > 500:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "问题请控制在 500 字以内"})
            return

        conversation_id = payload.get("conversationId")
        if conversation_id is None:
            conversation_id = str(uuid.uuid4())
        if not isinstance(conversation_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{8,80}", conversation_id
        ):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "会话编号无效"})
            return
        conversation_context = self.conversations.get(conversation_id)

        try:
            if self.agent_mode == "validated_llm":
                reply = answer_with_ai(
                    question,
                    self.database,
                    conversation_context=conversation_context,
                )
                self.conversations.put(conversation_id, reply.context)
                self.send_json(HTTPStatus.OK, {
                    "answer": reply.answer,
                    "report": reply.report,
                    "conversationId": conversation_id,
                    "meta": {
                        "mode": "validated_llm",
                        "decision": reply.decision,
                        "tool": reply.tool,
                        "parameters": reply.parameters,
                        "toolExecuted": reply.tool_executed,
                        "contextUsed": conversation_context is not None,
                    },
                })
            else:
                response = answer(question, self.database)
                self.send_json(HTTPStatus.OK, {
                    "answer": response,
                    "conversationId": conversation_id,
                    "meta": {"mode": "rules", "toolExecuted": True},
                })
        except AgentRuntimeError as exc:
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "error": f"AI 理解或安全校验暂时失败：{exc}"
            })
        except Exception:
            self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                "error": "查询暂时失败，请确认本机 PostgreSQL 正在运行后重试。"
            })

    def serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path == "/" else unquote(request_path.lstrip("/"))
        candidate = (WEB_DIR / relative).resolve()
        try:
            candidate.relative_to(WEB_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    load_local_environment()
    parser = argparse.ArgumentParser(description="超市经营助手本机网页")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--database",
        default=os.environ.get("SUPERMARKET_DATABASE", "supermarket_agent"),
    )
    parser.add_argument(
        "--agent-mode",
        choices=("rules", "validated_llm"),
        default=os.environ.get("AI_WEB_MODE", "rules"),
    )
    args = parser.parse_args()

    SalesAgentHandler.database = args.database
    SalesAgentHandler.agent_mode = args.agent_mode
    server = ThreadingHTTPServer((args.host, args.port), SalesAgentHandler)
    print(f"超市经营助手已启动：http://{args.host}:{args.port}", flush=True)
    print(f"问答模式：{args.agent_mode}", flush=True)
    print("按 Control+C 停止。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
