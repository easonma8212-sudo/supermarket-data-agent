"""Validated, read-only sales tools prepared for future AI function calling."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Callable

from sales_agent import Bounds, get_bounds, run_sql


class ToolInputError(ValueError):
    """Raised when tool arguments are outside the certified data boundary."""


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ToolInputError(f"{field} 必须是 YYYY-MM-DD 日期") from exc


def _validate_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
        raise ToolInputError("limit 必须是 1 至 50 的整数")
    return value


def _validate_category(value: str | None) -> str:
    if value is None:
        return ""
    clean = value.strip()
    if len(clean) > 80:
        raise ToolInputError("category 不能超过 80 个字符")
    return clean


def _validate_period(
    bounds: Bounds,
    start_date: str,
    end_date: str,
    *,
    allow_partial_end_date: bool = False,
) -> tuple[str, str]:
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    earliest = _parse_date(bounds.earliest, "earliest")
    latest_allowed = _parse_date(
        bounds.latest_date if allow_partial_end_date else bounds.latest_complete_date,
        "latest_allowed",
    )
    if start > end:
        raise ToolInputError("start_date 不能晚于 end_date")
    if start < earliest:
        raise ToolInputError(f"start_date 不能早于数据起始日 {earliest.isoformat()}")
    if end > latest_allowed:
        label = "最新数据日" if allow_partial_end_date else "最新完整日"
        raise ToolInputError(f"end_date 不能晚于{label} {latest_allowed.isoformat()}")
    return start.isoformat(), end.isoformat()


def _freshness(bounds: Bounds) -> dict[str, str]:
    return {
        "earliest_date": bounds.earliest,
        "latest_timestamp": bounds.latest_timestamp,
        "latest_data_date": bounds.latest_date,
        "latest_complete_date": bounds.latest_complete_date,
    }


def _result(
    tool: str,
    parameters: dict[str, Any],
    metrics: list[str],
    bounds: Bounds,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "tool": tool,
        "parameters": parameters,
        "metrics": metrics,
        "freshness": _freshness(bounds),
        "rows": rows,
    }


def query_sales_summary(
    database: str,
    *,
    start_date: str,
    end_date: str,
    allow_partial_end_date: bool = False,
) -> dict[str, Any]:
    bounds = get_bounds(database)
    start_date, end_date = _validate_period(
        bounds, start_date, end_date,
        allow_partial_end_date=allow_partial_end_date,
    )
    row = run_sql(database, """
SELECT coalesce(sum(actual_amount), 0),
       coalesce(sum(quantity), 0),
       count(DISTINCT receipt_no),
       CASE WHEN count(DISTINCT receipt_no) = 0 THEN NULL
            ELSE round(sum(actual_amount) / count(DISTINCT receipt_no), 2) END
FROM raw.sales_detail
WHERE sold_at::date BETWEEN CAST(:'start_date' AS date) AND CAST(:'end_date' AS date);
""", {"start_date": start_date, "end_date": end_date})[0]
    amount, quantity, receipts, average_receipt = row
    return _result(
        "query_sales_summary",
        {
            "start_date": start_date,
            "end_date": end_date,
            "allow_partial_end_date": allow_partial_end_date,
        },
        ["sales_amount", "sales_quantity", "receipt_count", "average_receipt_amount"],
        bounds,
        [{
            "sales_amount": amount,
            "sales_quantity": quantity,
            "receipt_count": int(receipts),
            "average_receipt_amount": average_receipt or None,
        }],
    )


def query_top_products(
    database: str,
    *,
    start_date: str,
    end_date: str,
    limit: int = 10,
    sort_by: str = "sales_amount",
    category: str | None = None,
) -> dict[str, Any]:
    bounds = get_bounds(database)
    start_date, end_date = _validate_period(bounds, start_date, end_date)
    limit = _validate_limit(limit)
    if sort_by not in {"sales_amount", "sales_quantity"}:
        raise ToolInputError("sort_by 只能是 sales_amount 或 sales_quantity")
    category = _validate_category(category)
    order_expression = "sum(actual_amount)" if sort_by == "sales_amount" else "sum(quantity)"
    rows = run_sql(database, f"""
SELECT item_no, max(product_name), coalesce(max(category), '未分类'),
       sum(quantity), sum(actual_amount), count(DISTINCT receipt_no)
FROM raw.sales_detail
WHERE sold_at::date BETWEEN CAST(:'start_date' AS date) AND CAST(:'end_date' AS date)
  AND (NULLIF(:'category', '') IS NULL OR category = NULLIF(:'category', ''))
GROUP BY item_no
ORDER BY {order_expression} DESC, item_no
LIMIT :limit;
""", {
        "start_date": start_date,
        "end_date": end_date,
        "category": category,
        "limit": limit,
    })
    result_rows = [{
        "item_no": item_no,
        "product_name": name,
        "category": row_category,
        "sales_quantity": quantity,
        "sales_amount": amount,
        "receipt_count": int(receipts),
    } for item_no, name, row_category, quantity, amount, receipts in rows]
    return _result(
        "query_top_products",
        {
            "start_date": start_date,
            "end_date": end_date,
            "limit": limit,
            "sort_by": sort_by,
            "category": category or None,
        },
        ["sales_amount", "sales_quantity", "receipt_count"],
        bounds,
        result_rows,
    )


def query_product_sales_change(
    database: str,
    *,
    current_start_date: str,
    current_end_date: str,
    previous_start_date: str,
    previous_end_date: str,
    limit: int = 5,
    direction: str = "both",
    category: str | None = None,
) -> dict[str, Any]:
    bounds = get_bounds(database)
    current_start_date, current_end_date = _validate_period(
        bounds, current_start_date, current_end_date
    )
    previous_start_date, previous_end_date = _validate_period(
        bounds, previous_start_date, previous_end_date
    )
    if _parse_date(previous_end_date, "previous_end_date") >= _parse_date(
        current_start_date, "current_start_date"
    ):
        raise ToolInputError("对比期间必须早于当前期间")
    limit = _validate_limit(limit)
    if direction not in {"both", "up", "down"}:
        raise ToolInputError("direction 只能是 both、up 或 down")
    category = _validate_category(category)
    rows = run_sql(database, """
WITH changes AS (
    SELECT item_no, max(product_name) AS product_name,
           coalesce(max(category), '未分类') AS category,
           coalesce(sum(quantity) FILTER (
               WHERE sold_at::date BETWEEN CAST(:'current_start' AS date)
                                       AND CAST(:'current_end' AS date)
           ), 0) AS current_quantity,
           coalesce(sum(quantity) FILTER (
               WHERE sold_at::date BETWEEN CAST(:'previous_start' AS date)
                                       AND CAST(:'previous_end' AS date)
           ), 0) AS previous_quantity
    FROM raw.sales_detail
    WHERE sold_at::date BETWEEN CAST(:'previous_start' AS date)
                            AND CAST(:'current_end' AS date)
      AND (NULLIF(:'category', '') IS NULL OR category = NULLIF(:'category', ''))
    GROUP BY item_no
), ranked AS (
    SELECT *, current_quantity - previous_quantity AS quantity_delta,
           CASE WHEN current_quantity > previous_quantity THEN 'up'
                WHEN current_quantity < previous_quantity THEN 'down'
                ELSE 'flat' END AS direction
    FROM changes
), numbered AS (
    SELECT *, row_number() OVER (
        PARTITION BY direction ORDER BY abs(quantity_delta) DESC, item_no
    ) AS direction_rank
    FROM ranked
    WHERE direction <> 'flat'
)
SELECT item_no, product_name, category, previous_quantity, current_quantity,
       quantity_delta, direction
FROM numbered
WHERE direction_rank <= :limit
  AND (:'direction' = 'both' OR direction = :'direction')
ORDER BY CASE direction WHEN 'up' THEN 1 ELSE 2 END,
         abs(quantity_delta) DESC, item_no;
""", {
        "current_start": current_start_date,
        "current_end": current_end_date,
        "previous_start": previous_start_date,
        "previous_end": previous_end_date,
        "category": category,
        "limit": limit,
        "direction": direction,
    })
    result_rows = [{
        "item_no": item_no,
        "product_name": name,
        "category": row_category,
        "previous_quantity": previous,
        "current_quantity": current,
        "quantity_delta": delta,
        "direction": row_direction,
    } for item_no, name, row_category, previous, current, delta, row_direction in rows]
    return _result(
        "query_product_sales_change",
        {
            "current_start_date": current_start_date,
            "current_end_date": current_end_date,
            "previous_start_date": previous_start_date,
            "previous_end_date": previous_end_date,
            "limit": limit,
            "direction": direction,
            "category": category or None,
        },
        ["product_sales_change", "sales_quantity"],
        bounds,
        result_rows,
    )


def query_category_contribution(
    database: str,
    *,
    start_date: str,
    end_date: str,
    limit: int = 10,
) -> dict[str, Any]:
    bounds = get_bounds(database)
    start_date, end_date = _validate_period(bounds, start_date, end_date)
    limit = _validate_limit(limit)
    rows = run_sql(database, """
WITH category_sales AS (
    SELECT coalesce(category, '未分类') AS category,
           sum(actual_amount) AS sales_amount,
           sum(quantity) AS sales_quantity
    FROM raw.sales_detail
    WHERE sold_at::date BETWEEN CAST(:'start_date' AS date) AND CAST(:'end_date' AS date)
    GROUP BY coalesce(category, '未分类')
), total AS (SELECT sum(sales_amount) AS sales_amount FROM category_sales)
SELECT c.category, c.sales_amount, c.sales_quantity,
       CASE WHEN t.sales_amount = 0 THEN NULL
            ELSE round(c.sales_amount / t.sales_amount * 100, 1) END
FROM category_sales c CROSS JOIN total t
ORDER BY c.sales_amount DESC, c.category
LIMIT :limit;
""", {"start_date": start_date, "end_date": end_date, "limit": limit})
    result_rows = [{
        "category": category,
        "sales_amount": amount,
        "sales_quantity": quantity,
        "contribution_percent": share,
    } for category, amount, quantity, share in rows]
    return _result(
        "query_category_contribution",
        {"start_date": start_date, "end_date": end_date, "limit": limit},
        ["category_contribution", "sales_amount", "sales_quantity"],
        bounds,
        result_rows,
    )


def query_kpi_comparison(
    database: str,
    *,
    current_start_date: str,
    current_end_date: str,
    previous_start_date: str,
    previous_end_date: str,
) -> dict[str, Any]:
    bounds = get_bounds(database)
    current_start_date, current_end_date = _validate_period(
        bounds, current_start_date, current_end_date
    )
    previous_start_date, previous_end_date = _validate_period(
        bounds, previous_start_date, previous_end_date
    )
    if _parse_date(previous_end_date, "previous_end_date") >= _parse_date(
        current_start_date, "current_start_date"
    ):
        raise ToolInputError("对比期间必须早于当前期间")
    rows = run_sql(database, """
WITH periods AS (
    SELECT 'current' AS period, CAST(:'current_start' AS date) AS start_date,
           CAST(:'current_end' AS date) AS end_date
    UNION ALL
    SELECT 'previous', CAST(:'previous_start' AS date), CAST(:'previous_end' AS date)
), metrics AS (
    SELECT p.period, p.start_date, p.end_date,
           coalesce(sum(s.actual_amount), 0) AS sales_amount,
           coalesce(sum(s.quantity), 0) AS sales_quantity,
           count(DISTINCT s.receipt_no) AS receipt_count
    FROM periods p
    LEFT JOIN raw.sales_detail s ON s.sold_at::date BETWEEN p.start_date AND p.end_date
    GROUP BY p.period, p.start_date, p.end_date
)
SELECT period, start_date, end_date, sales_amount, sales_quantity, receipt_count,
       CASE WHEN receipt_count = 0 THEN NULL
            ELSE round(sales_amount / receipt_count, 2) END
FROM metrics
ORDER BY CASE period WHEN 'current' THEN 1 ELSE 2 END;
""", {
        "current_start": current_start_date,
        "current_end": current_end_date,
        "previous_start": previous_start_date,
        "previous_end": previous_end_date,
    })
    parsed = [{
        "period": period,
        "start_date": start,
        "end_date": end,
        "sales_amount": amount,
        "sales_quantity": quantity,
        "receipt_count": int(receipts),
        "average_receipt_amount": average_receipt or None,
    } for period, start, end, amount, quantity, receipts, average_receipt in rows]
    return _result(
        "query_kpi_comparison",
        {
            "current_start_date": current_start_date,
            "current_end_date": current_end_date,
            "previous_start_date": previous_start_date,
            "previous_end_date": previous_end_date,
        },
        ["sales_amount", "sales_quantity", "receipt_count", "average_receipt_amount"],
        bounds,
        parsed,
    )


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "query_sales_summary",
        "description": "查询指定日期范围的销售额、销量、小票数和客单价。",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "开始日期，YYYY-MM-DD。"},
                "end_date": {"type": "string", "description": "结束日期，YYYY-MM-DD。"},
                "allow_partial_end_date": {
                    "type": "boolean",
                    "description": "是否允许结束日期为可能不完整的最新数据日。",
                },
            },
            "required": ["start_date", "end_date", "allow_partial_end_date"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "query_top_products",
        "description": "查询指定期间的畅销商品，可按销售额或销量排序。",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "sort_by": {"type": "string", "enum": ["sales_amount", "sales_quantity"]},
                "category": {"type": ["string", "null"]},
            },
            "required": ["start_date", "end_date", "limit", "sort_by", "category"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "query_product_sales_change",
        "description": "比较两个不重叠期间的商品销量变化。",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "current_start_date": {"type": "string"},
                "current_end_date": {"type": "string"},
                "previous_start_date": {"type": "string"},
                "previous_end_date": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "direction": {"type": "string", "enum": ["both", "up", "down"]},
                "category": {"type": ["string", "null"]},
            },
            "required": [
                "current_start_date", "current_end_date", "previous_start_date",
                "previous_end_date", "limit", "direction", "category"
            ],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "query_category_contribution",
        "description": "查询指定期间各类别的销售贡献。",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["start_date", "end_date", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "query_kpi_comparison",
        "description": "比较两个期间的销售额、销量、小票数和客单价。",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "current_start_date": {"type": "string"},
                "current_end_date": {"type": "string"},
                "previous_start_date": {"type": "string"},
                "previous_end_date": {"type": "string"},
            },
            "required": [
                "current_start_date", "current_end_date",
                "previous_start_date", "previous_end_date"
            ],
            "additionalProperties": False,
        },
    },
]


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "query_sales_summary": query_sales_summary,
    "query_top_products": query_top_products,
    "query_product_sales_change": query_product_sales_change,
    "query_category_contribution": query_category_contribution,
    "query_kpi_comparison": query_kpi_comparison,
}


def invoke_tool(name: str, arguments: dict[str, Any], database: str = "supermarket_agent") -> dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolInputError(f"未知工具：{name}")
    if not isinstance(arguments, dict):
        raise ToolInputError("工具参数必须是对象")
    return tool(database, **arguments)
