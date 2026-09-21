#!/usr/bin/env python3
"""Evaluate the deterministic sales agent against its versioned Golden Set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from sales_agent import answer, classify_intent, get_bounds, run_sql  # noqa: E402


DEFAULT_GOLDEN_SET = ROOT / "evals" / "sales_agent_golden_set.json"


def load_golden_set(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def check_dataset_snapshot(spec: dict, database: str) -> list[str]:
    bounds = get_bounds(database)
    line_count = int(run_sql(database, "SELECT count(*) FROM raw.sales_detail;")[0][0])
    actual = {
        "earliest_date": bounds.earliest,
        "latest_timestamp": bounds.latest_timestamp,
        "latest_complete_date": bounds.latest_complete_date,
        "sales_line_count": line_count,
    }
    return [
        f"dataset.{key}: expected {expected!r}, got {actual[key]!r}"
        for key, expected in spec.items()
        if key != "database" and actual.get(key) != expected
    ]


def evaluate(spec: dict, database: str, live: bool) -> list[str]:
    failures: list[str] = []
    for case in spec["cases"]:
        actual_intent = classify_intent(case["question"])
        if actual_intent != case["expected_intent"]:
            failures.append(
                f"{case['id']}: intent expected {case['expected_intent']!r}, "
                f"got {actual_intent!r}"
            )
            continue
        if live and case.get("live"):
            response = answer(case["question"], database)
            for fragment in case.get("expected_contains", []):
                if fragment not in response:
                    failures.append(f"{case['id']}: missing response fragment {fragment!r}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="运行超市经营问答 Golden Set")
    parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--database", default="supermarket_agent")
    parser.add_argument(
        "--live",
        action="store_true",
        help="同时连接本机 PostgreSQL，核对五个基准问题的真实答案",
    )
    args = parser.parse_args()

    spec = load_golden_set(args.golden_set)
    failures: list[str] = []
    if args.live:
        failures.extend(check_dataset_snapshot(spec["dataset_snapshot"], args.database))
    failures.extend(evaluate(spec, args.database, args.live))

    mode = "意图 + 真实数据" if args.live else "意图"
    if failures:
        print(f"Golden Set 失败（{mode}）：{len(failures)} 项")
        for failure in failures:
            print(f"- {failure}")
        return 1

    live_count = sum(1 for case in spec["cases"] if case.get("live"))
    suffix = f"，其中 {live_count} 个核对真实答案" if args.live else ""
    print(f"Golden Set 通过（{mode}）：{len(spec['cases'])} 个问题{suffix}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
