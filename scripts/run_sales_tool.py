#!/usr/bin/env python3
"""Run one certified sales tool with JSON arguments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from sales_tools import TOOL_SCHEMAS, ToolInputError, invoke_tool  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="运行认证经营分析工具")
    parser.add_argument("tool", nargs="?", help="工具名称")
    parser.add_argument("--arguments", default="{}", help="JSON 格式工具参数")
    parser.add_argument("--database", default="supermarket_agent")
    parser.add_argument("--list", action="store_true", help="列出工具及严格参数结构")
    args = parser.parse_args()

    if args.list:
        print(json.dumps(TOOL_SCHEMAS, ensure_ascii=False, indent=2))
        return 0
    if not args.tool:
        parser.error("请提供工具名称，或使用 --list")
    try:
        arguments = json.loads(args.arguments)
        result = invoke_tool(args.tool, arguments, args.database)
    except (json.JSONDecodeError, ToolInputError, TypeError) as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
