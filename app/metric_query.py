"""Governed NL2Metrics plan validation, entity resolution, and SQL compilation."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sales_agent import Bounds, get_bounds, run_sql


METRICS = {
    "sales_amount": ("coalesce(sum(actual_amount), 0)", "销售额"),
    "sales_quantity": ("coalesce(sum(quantity), 0)", "销量"),
    "receipt_count": ("count(DISTINCT receipt_no)", "小票数"),
    "average_receipt_amount": (
        "CASE WHEN count(DISTINCT receipt_no) = 0 THEN NULL "
        "ELSE round(sum(actual_amount) / count(DISTINCT receipt_no), 2) END",
        "客单价",
    ),
    "average_selling_price": (
        "CASE WHEN sum(quantity) = 0 THEN NULL "
        "ELSE round(sum(actual_amount) / sum(quantity), 2) END",
        "平均售价",
    ),
}
DIMENSIONS = {"product", "category", "day"}


class MetricPlanError(ValueError):
    """Raised when a semantic query plan is outside the governed metric model."""


class EntityClarification(MetricPlanError):
    """Raised when a product name cannot be resolved to one item safely."""

    def __init__(self, question: str, candidates: list[dict[str, str]] | None = None):
        super().__init__(question)
        self.question = question
        self.candidates = candidates or []


METRIC_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "name": "query_metrics",
    "description": (
        "通用 NL2Metrics 查询。用于指定商品、类别或日期维度的销售额、销量、小票数、"
        "客单价和平均售价；支持商品名称筛选、前期对比、排序和限制。"
    ),
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "metrics": {
                "type": "array",
                "items": {"type": "string", "enum": list(METRICS)},
                "minItems": 1,
                "maxItems": 5,
            },
            "dimension": {
                "type": ["string", "null"],
                "enum": ["product", "category", "day", None],
                "description": "不分组时为 null；商品排行用 product，类别排行用 category，趋势用 day。",
            },
            "filters": {
                "type": "object",
                "properties": {
                    "product_query": {
                        "type": ["string", "null"],
                        "description": "用户指定的商品名称或商品编号；未指定时为 null。",
                    },
                    "category": {
                        "type": ["string", "null"],
                        "description": "精确类别名称；未指定时为 null。",
                    },
                },
                "required": ["product_query", "category"],
                "additionalProperties": False,
            },
            "start_date": {"type": "string", "description": "YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            "allow_partial_end_date": {"type": "boolean"},
            "comparison": {
                "type": "string",
                "enum": ["none", "previous_period"],
                "description": "previous_period 使用紧邻此前的等长期间。",
            },
            "order_by": {
                "type": ["string", "null"],
                "enum": [*METRICS, None],
            },
            "order_direction": {"type": "string", "enum": ["asc", "desc"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": [
            "metrics", "dimension", "filters", "start_date", "end_date",
            "allow_partial_end_date", "comparison", "order_by",
            "order_direction", "limit",
        ],
        "additionalProperties": False,
    },
}


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise MetricPlanError(f"{field} 必须是 YYYY-MM-DD 日期") from exc


def _validate_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MetricPlanError(f"{field} 必须是字符串或 null")
    clean = value.strip()
    if not clean:
        return None
    if len(clean) > 80:
        raise MetricPlanError(f"{field} 不能超过 80 个字符")
    return clean


def _validate_plan(plan: dict[str, Any], bounds: Bounds) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise MetricPlanError("指标查询计划必须是对象")
    metrics = plan.get("metrics")
    if not isinstance(metrics, list) or not metrics or len(metrics) > len(METRICS):
        raise MetricPlanError("metrics 必须包含 1 至 5 个指标")
    if len(metrics) != len(set(metrics)) or any(metric not in METRICS for metric in metrics):
        raise MetricPlanError("metrics 包含重复或未认证指标")
    dimension = plan.get("dimension")
    if dimension is not None and dimension not in DIMENSIONS:
        raise MetricPlanError("dimension 只能是 product、category、day 或 null")
    filters = plan.get("filters")
    if not isinstance(filters, dict) or set(filters) != {"product_query", "category"}:
        raise MetricPlanError("filters 结构无效")
    product_query = _validate_text(filters["product_query"], "product_query")
    category = _validate_text(filters["category"], "category")
    start = _parse_date(plan.get("start_date"), "start_date")
    end = _parse_date(plan.get("end_date"), "end_date")
    earliest = _parse_date(bounds.earliest, "earliest")
    allow_partial = plan.get("allow_partial_end_date")
    if not isinstance(allow_partial, bool):
        raise MetricPlanError("allow_partial_end_date 必须是布尔值")
    latest = _parse_date(
        bounds.latest_date if allow_partial else bounds.latest_complete_date,
        "latest_allowed",
    )
    if start > end:
        raise MetricPlanError("start_date 不能晚于 end_date")
    if start < earliest:
        raise MetricPlanError(f"start_date 不能早于数据起始日 {earliest}")
    if end > latest:
        label = "最新数据日" if allow_partial else "最新完整日"
        raise MetricPlanError(f"end_date 不能晚于{label} {latest}")
    comparison = plan.get("comparison")
    if comparison not in {"none", "previous_period"}:
        raise MetricPlanError("comparison 只能是 none 或 previous_period")
    if comparison == "previous_period":
        days = (end - start).days + 1
        previous_start = start - timedelta(days=days)
        if previous_start < earliest:
            raise MetricPlanError("此前对比期间早于数据起始日")
    order_by = plan.get("order_by")
    if order_by is not None and order_by not in METRICS:
        raise MetricPlanError("order_by 不是认证指标")
    if order_by is not None and order_by not in metrics:
        raise MetricPlanError("order_by 必须同时出现在 metrics 中")
    order_direction = plan.get("order_direction")
    if order_direction not in {"asc", "desc"}:
        raise MetricPlanError("order_direction 只能是 asc 或 desc")
    limit = plan.get("limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise MetricPlanError("limit 必须是 1 至 50 的整数")
    normalized_limit = limit if dimension is not None else 1
    if dimension == "day" and order_by is None:
        normalized_limit = min((end - start).days + 1, 50)
    return {
        "metrics": metrics,
        "dimension": dimension,
        "filters": {"product_query": product_query, "category": category},
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "allow_partial_end_date": allow_partial,
        "comparison": comparison,
        "order_by": order_by,
        "order_direction": order_direction,
        "limit": normalized_limit,
    }


def resolve_product(database: str, product_query: str) -> dict[str, str]:
    exact = run_sql(database, """
SELECT item_no, max(product_name), coalesce(max(category), '未分类')
FROM raw.sales_detail
WHERE item_no = :'product_query' OR lower(product_name) = lower(:'product_query')
GROUP BY item_no
ORDER BY item_no
LIMIT 6;
""", {"product_query": product_query})
    rows = exact
    if not rows:
        rows = run_sql(database, """
SELECT item_no, max(product_name), coalesce(max(category), '未分类')
FROM raw.sales_detail
WHERE product_name ILIKE '%' || :'product_query' || '%'
GROUP BY item_no
ORDER BY sum(actual_amount) DESC, item_no
LIMIT 6;
""", {"product_query": product_query})
    candidates = [
        {"item_no": item_no, "product_name": name, "category": category}
        for item_no, name, category in rows
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise EntityClarification(
            f"没有找到“{product_query}”。请提供更完整的商品名称或商品编号。"
        )
    labels = "、".join(
        f"{item['product_name']}（{item['item_no']}）" for item in candidates[:5]
    )
    raise EntityClarification(
        f"“{product_query}”匹配到多个商品：{labels}。请指定商品名称或编号。",
        candidates,
    )


def _compile_query(
    plan: dict[str, Any],
    start_date: str,
    end_date: str,
    resolved_product: dict[str, str] | None,
) -> tuple[str, dict[str, Any], list[str]]:
    dimension = plan["dimension"]
    columns: list[str] = []
    select_parts: list[str] = []
    group_by: list[str] = []
    if dimension == "product":
        select_parts.extend([
            "item_no AS item_no",
            "max(product_name) AS product_name",
            "coalesce(max(category), '未分类') AS category",
        ])
        columns.extend(["item_no", "product_name", "category"])
        group_by.append("item_no")
    elif dimension == "category":
        select_parts.append("coalesce(category, '未分类') AS category")
        columns.append("category")
        group_by.append("coalesce(category, '未分类')")
    elif dimension == "day":
        select_parts.append("sold_at::date AS day")
        columns.append("day")
        group_by.append("sold_at::date")

    for metric in plan["metrics"]:
        expression, _ = METRICS[metric]
        select_parts.append(f"{expression} AS {metric}")
        columns.append(metric)

    where_parts = [
        "sold_at::date BETWEEN CAST(:'start_date' AS date) AND CAST(:'end_date' AS date)"
    ]
    variables: dict[str, Any] = {"start_date": start_date, "end_date": end_date}
    if resolved_product:
        where_parts.append("item_no = :'item_no'")
        variables["item_no"] = resolved_product["item_no"]
    if plan["filters"]["category"]:
        where_parts.append("category = :'category'")
        variables["category"] = plan["filters"]["category"]

    sql = "SELECT " + ", ".join(select_parts)
    sql += " FROM raw.sales_detail WHERE " + " AND ".join(where_parts)
    if group_by:
        sql += " GROUP BY " + ", ".join(group_by)
    if dimension is not None:
        if plan["order_by"]:
            sql += f" ORDER BY {plan['order_by']} {plan['order_direction'].upper()} NULLS LAST"
        else:
            sql += f" ORDER BY {columns[0]} ASC"
        sql += " LIMIT :limit"
        variables["limit"] = plan["limit"]
    sql += ";"
    return sql, variables, columns


def _run_period(
    database: str,
    plan: dict[str, Any],
    start_date: str,
    end_date: str,
    resolved_product: dict[str, str] | None,
) -> dict[str, Any]:
    sql, variables, columns = _compile_query(
        plan, start_date, end_date, resolved_product
    )
    raw_rows = run_sql(database, sql, variables)
    rows = [dict(zip(columns, row)) for row in raw_rows]
    for row in rows:
        if "receipt_count" in row:
            row["receipt_count"] = int(row["receipt_count"])
    return {"start_date": start_date, "end_date": end_date, "rows": rows}


def execute_metric_query(
    database: str = "supermarket_agent",
    **raw_plan: Any,
) -> dict[str, Any]:
    bounds = get_bounds(database)
    plan = _validate_plan(raw_plan, bounds)
    product_query = plan["filters"]["product_query"]
    resolved_product = resolve_product(database, product_query) if product_query else None
    periods = [{
        "label": "current",
        **_run_period(
            database, plan, plan["start_date"], plan["end_date"], resolved_product
        ),
    }]
    if plan["comparison"] == "previous_period":
        start = date.fromisoformat(plan["start_date"])
        end = date.fromisoformat(plan["end_date"])
        days = (end - start).days + 1
        previous_end = start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=days - 1)
        periods.append({
            "label": "previous",
            **_run_period(
                database,
                plan,
                previous_start.isoformat(),
                previous_end.isoformat(),
                resolved_product,
            ),
        })
    return {
        "tool": "query_metrics",
        "parameters": {
            **plan,
            "resolved_product": resolved_product,
        },
        "metric_labels": {metric: METRICS[metric][1] for metric in plan["metrics"]},
        "periods": periods,
    }
