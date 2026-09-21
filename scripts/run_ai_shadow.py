#!/usr/bin/env python3
"""Compare an AI tool plan with the current deterministic router without executing it."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from ai_planner import AIPlannerError, OpenAIShadowPlanner, load_local_environment  # noqa: E402
from sales_agent import classify_intent, get_bounds  # noqa: E402


def main() -> int:
    load_local_environment()
    parser = argparse.ArgumentParser(description="AI 工具调用影子模式")
    parser.add_argument("question", nargs="*", help="中文经营问题")
    parser.add_argument("--database", default=os.environ.get("SUPERMARKET_DATABASE", "supermarket_agent"))
    parser.add_argument("--check", action="store_true", help="仅检查本机 API 配置，不发送请求")
    args = parser.parse_args()

    if args.check:
        print(json.dumps({
            "api_key_configured": bool(os.environ.get("OPENAI_API_KEY")),
            "model_configured": bool(os.environ.get("OPENAI_MODEL")),
            "shadow_mode": True,
            "executes_database_tools": False,
        }, ensure_ascii=False, indent=2))
        return 0
    if not args.question:
        parser.error("请提供一个经营问题，或使用 --check")

    question = " ".join(args.question).strip()
    try:
        planner = OpenAIShadowPlanner.from_environment()
        bounds = get_bounds(args.database)
        plan = planner.plan(question, bounds)
    except AIPlannerError as exc:
        print(f"影子模式未运行：{exc}", file=sys.stderr)
        return 2

    print(json.dumps({
        "question": question,
        "current_rule_intent": classify_intent(question),
        "ai_plan": plan,
        "tool_executed": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
