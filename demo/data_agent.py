"""A deterministic, synthetic data-agent vertical slice.

No external data, model, network request, or outbound notification is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
from typing import Iterable


CATALOG = {
    "gmv_demo_usd": {
        "version": "1.0.0",
        "label": "示例支付金额",
        "definition": "虚构订单中状态为 paid 的金额之和，单位 USD",
        "grain": "day",
        "dimensions": ["market"],
    }
}


@dataclass(frozen=True)
class Order:
    day: date
    market: str
    amount_cents: int
    status: str


@dataclass(frozen=True)
class Principal:
    name: str
    allowed_markets: frozenset[str]


class AgentError(ValueError):
    pass


def synthetic_orders() -> list[Order]:
    """A small, reproducible fixture; day 2 declines in DE and rises in US."""
    start = date(2026, 9, 14)
    return [
        Order(start, "DE", 10000, "paid"),
        Order(start, "US", 20000, "paid"),
        Order(start, "DE", 99900, "refunded"),
        Order(start + timedelta(days=1), "DE", 8000, "paid"),
        Order(start + timedelta(days=1), "US", 21000, "paid"),
    ]


def _query_id(metric: str, day: date, markets: Iterable[str]) -> str:
    raw = json.dumps([metric, day.isoformat(), sorted(markets)], separators=(",", ":"))
    return sha256(raw.encode()).hexdigest()[:16]


def query_metric(
    orders: Iterable[Order],
    principal: Principal,
    metric: str,
    day: date,
    markets: Iterable[str],
    as_of: date,
) -> dict:
    """Query a certified metric after authorization and freshness checks."""
    if metric not in CATALOG:
        raise AgentError("未知或未认证的指标")
    requested = frozenset(markets)
    if not requested:
        raise AgentError("必须显式指定市场")
    if not requested.issubset(principal.allowed_markets):
        raise PermissionError("请求包含未授权市场")
    if day >= as_of:
        raise AgentError("该日期的数据尚未完成日批处理")
    rows = [
        row for row in orders
        if row.day == day and row.market in requested and row.status == "paid"
    ]
    if not rows:
        raise AgentError("没有可用数据；不能把缺数当作零")
    by_market = {market: sum(r.amount_cents for r in rows if r.market == market) / 100
                 for market in sorted(requested)}
    if any(market not in {r.market for r in rows} for market in requested):
        raise AgentError("所选市场数据不完整")
    total = round(sum(by_market.values()), 2)
    spec = CATALOG[metric]
    return {
        "metric": metric,
        "value": total,
        "unit": "USD",
        "by_market": by_market,
        "day": day.isoformat(),
        "as_of": as_of.isoformat(),
        "evidence": {
            "query_id": _query_id(metric, day, requested),
            "metric_version": spec["version"],
            "definition": spec["definition"],
            "filters": {"markets": sorted(requested), "status": "paid"},
            "row_count": len(rows),
        },
    }


def decompose_change(previous: dict, current: dict) -> dict:
    """Arithmetic decomposition; never claims a causal root cause."""
    if previous["metric"] != current["metric"]:
        raise AgentError("指标不一致")
    if previous["evidence"]["filters"] != current["evidence"]["filters"]:
        raise AgentError("筛选范围不一致")
    old = previous["value"]
    new = current["value"]
    if old == 0:
        raise AgentError("基期为零，无法计算百分比")
    delta = round(new - old, 2)
    contributions = {
        market: round(current["by_market"][market] - previous["by_market"][market], 2)
        for market in previous["by_market"]
    }
    if abs(sum(contributions.values()) - delta) > 0.01:
        raise AgentError("维度分解不能对齐总变化")
    return {
        "delta_usd": delta,
        "change_pct": round(100 * delta / old, 2),
        "contributions_usd": contributions,
        "interpretation": "仅表示各市场对总额变化的算术贡献，不证明因果关系",
        "query_ids": [previous["evidence"]["query_id"], current["evidence"]["query_id"]],
    }


def run_demo() -> dict:
    orders = synthetic_orders()
    principal = Principal("demo_manager", frozenset({"DE", "US"}))
    previous = query_metric(orders, principal, "gmv_demo_usd", date(2026, 9, 14),
                            {"DE", "US"}, date(2026, 9, 16))
    current = query_metric(orders, principal, "gmv_demo_usd", date(2026, 9, 15),
                           {"DE", "US"}, date(2026, 9, 16))
    return {"previous": previous, "current": current,
            "decomposition": decompose_change(previous, current)}


if __name__ == "__main__":
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))
