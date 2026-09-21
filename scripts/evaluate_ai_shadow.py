#!/usr/bin/env python3
"""Evaluate the OpenAI shadow planner without executing its selected tools."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from ai_planner import AIPlannerError, OpenAIShadowPlanner, load_local_environment  # noqa: E402
from sales_agent import get_bounds  # noqa: E402


DEFAULT_GOLDEN_SET = ROOT / "evals" / "ai_shadow_golden_set.json"


def load_golden_set(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def compare_plan(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for field in ("decision", "tool"):
        if actual.get(field) != expected.get(field):
            failures.append(
                f"{field} expected {expected.get(field)!r}, got {actual.get(field)!r}"
            )
    if "arguments" in expected and actual.get("arguments") != expected["arguments"]:
        failures.append(
            "arguments expected "
            f"{json.dumps(expected['arguments'], ensure_ascii=False, sort_keys=True)}, got "
            f"{json.dumps(actual.get('arguments'), ensure_ascii=False, sort_keys=True)}"
        )
    return failures


def evaluate(
    spec: dict[str, Any],
    planner: OpenAIShadowPlanner,
    database: str,
    selected_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    bounds = get_bounds(database)
    results: list[dict[str, Any]] = []
    failed = 0
    for case in spec["cases"]:
        if selected_ids and case["id"] not in selected_ids:
            continue
        try:
            plan = planner.plan(case["question"], bounds)
            failures = compare_plan(case["expected"], plan)
            safe_plan = {
                "decision": plan["decision"],
                "tool": plan["tool"],
                "arguments": plan["arguments"],
                "model": plan.get("model"),
            }
        except AIPlannerError as exc:
            failures = [str(exc)]
            safe_plan = None
        passed = not failures
        failed += int(not passed)
        results.append({
            "id": case["id"],
            "question": case["question"],
            "passed": passed,
            "failures": failures,
            "actual": safe_plan,
            "tool_executed": False,
        })
    return results, failed


def main() -> int:
    load_local_environment()
    parser = argparse.ArgumentParser(description="运行 AI 规划器影子 Golden Set")
    parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--database", default="supermarket_agent")
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="只运行指定案例 ID；可重复使用",
    )
    parser.add_argument("--json", action="store_true", help="输出完整 JSON 结果")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="每个选中案例重复运行次数，用于检测模型决策漂移（1-20）",
    )
    args = parser.parse_args()

    if not 1 <= args.repeat <= 20:
        parser.error("--repeat 必须是 1 至 20")

    spec = load_golden_set(args.golden_set)
    known_ids = {case["id"] for case in spec["cases"]}
    selected_ids = set(args.case_ids) if args.case_ids else None
    unknown_ids = (selected_ids or set()) - known_ids
    if unknown_ids:
        parser.error(f"未知案例：{', '.join(sorted(unknown_ids))}")

    try:
        planner = OpenAIShadowPlanner.from_environment()
        results = []
        failed = 0
        for run_number in range(1, args.repeat + 1):
            run_results, run_failed = evaluate(spec, planner, args.database, selected_ids)
            for result in run_results:
                result["run"] = run_number
            results.extend(run_results)
            failed += run_failed
    except AIPlannerError as exc:
        print(f"影子评估未运行：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "version": spec["version"],
            "total": len(results),
            "passed": len(results) - failed,
            "failed": failed,
            "results": results,
        }, ensure_ascii=False, indent=2))
    else:
        for result in results:
            mark = "PASS" if result["passed"] else "FAIL"
            tool = result["actual"]["tool"] if result["actual"] else "request_error"
            run_suffix = f"#{result['run']}" if args.repeat > 1 else ""
            print(f"{mark} {result['id']}{run_suffix}: {tool}")
            for failure in result["failures"]:
                print(f"  - {failure}")
        print(
            f"影子评估：{len(results) - failed}/{len(results)} 通过；"
            "数据库工具执行次数：0。"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
