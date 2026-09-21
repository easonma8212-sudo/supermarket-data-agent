"""A deterministic Chinese query interface for supermarket sales analysis."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import re
import subprocess
import sys


@dataclass(frozen=True)
class Bounds:
    earliest: str
    latest_timestamp: str
    latest_date: str
    latest_complete_date: str


def classify_intent(question: str) -> str:
    text = question.strip()
    unsupported_words = (
        "为什么", "原因", "利润", "毛利", "库存", "补货", "采购",
        "供应商", "会员", "预测", "明天", "未来",
    )
    if any(word in text for word in unsupported_words):
        return "unsupported"

    change_words = (
        "变化", "趋势", "上升", "下降", "增长", "减少", "对比",
        "变好", "变差", "卖少", "带动",
    )
    kpi_words = ("客单价", "订单量", "小票", "单量")
    if any(word in text for word in change_words) and any(word in text for word in kpi_words):
        return "kpi_change"
    if any(word in text for word in ("类别", "分类", "品类", "贡献", "类型", "大类")):
        return "category_contribution"
    if any(word in text for word in (
        "最好", "畅销", "热销", "卖得最多", "卖得多", "销售最高",
        "销售额最高", "最高的商品", "商品排行", "商品排名", "销售排行",
        "前十名商品",
    )):
        return "top_products"
    if any(word in text for word in change_words):
        return "product_change"
    if any(word in text for word in (
        "销售额", "营业额", "销售", "今天", "今日", "昨天", "昨日",
        "本周", "这周", "生意", "卖了多少", "客单价", "订单量", "小票"
    )):
        return "sales_summary"
    return "help"


def run_sql(
    database: str,
    sql: str,
    variables: dict[str, str | int | Decimal] | None = None,
) -> list[list[str]]:
    command = [
        "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", database,
        "-A", "-t", "-F", "\t",
    ]
    for name, value in (variables or {}).items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid psql variable name: {name}")
        command.extend(["-v", f"{name}={value}"])
    result = subprocess.run(
        command,
        input=sql,
        text=True,
        capture_output=True,
        check=True,
    )
    return [line.split("\t") for line in result.stdout.splitlines() if line]


def get_bounds(database: str) -> Bounds:
    row = run_sql(
        database,
        "SELECT earliest_sale_timestamp::date, latest_sale_timestamp, "
        "latest_data_date, latest_complete_date FROM analytics.data_freshness;",
    )[0]
    return Bounds(*row)


def money(value: str) -> str:
    return f"{Decimal(value or '0'):,.2f} 元"


def quantity(value: str) -> str:
    number = Decimal(value or "0")
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def freshness_note(bounds: Bounds) -> str:
    return (
        f"数据覆盖 {bounds.earliest} 至 {bounds.latest_timestamp}。"
        f"最新日期可能未完整；趋势计算截止 {bounds.latest_complete_date}。"
    )


def sales_summary(database: str, bounds: Bounds) -> str:
    from sales_tools import query_sales_summary

    latest_date = date.fromisoformat(bounds.latest_date)
    complete_date = date.fromisoformat(bounds.latest_complete_date)
    week_start = latest_date - timedelta(days=latest_date.weekday())
    periods = [
        ("最新日期（部分日）", latest_date, latest_date, True),
        ("最近完整日", complete_date, complete_date, False),
        ("最新数据所在周", week_start, latest_date, True),
    ]
    lines = [freshness_note(bounds), "销售概览："]
    for label, start_date, end_date, allow_partial in periods:
        result = query_sales_summary(
            database,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            allow_partial_end_date=allow_partial,
        )
        row = result["rows"][0]
        amount = row["sales_amount"]
        receipts = row["receipt_count"]
        avg_receipt = row["average_receipt_amount"]
        start_date = start_date.isoformat()
        end_date = end_date.isoformat()
        period = start_date if start_date == end_date else f"{start_date} 至 {end_date}"
        avg_text = "无可用小票" if not avg_receipt else money(avg_receipt)
        lines.append(
            f"- {label}（{period}）：销售额 {money(amount)}，"
            f"小票 {receipts} 张，客单价 {avg_text}"
        )
    return "\n".join(lines)


def top_products(database: str, bounds: Bounds) -> str:
    from sales_tools import query_top_products

    end_date = date.fromisoformat(bounds.latest_complete_date)
    result = query_top_products(
        database,
        start_date=(end_date - timedelta(days=6)).isoformat(),
        end_date=end_date.isoformat(),
        limit=10,
        sort_by="sales_amount",
        category=None,
    )
    lines = [freshness_note(bounds), f"最近完整7天畅销商品（截止 {bounds.latest_complete_date}，按销售额）："]
    for index, row in enumerate(result["rows"], start=1):
        lines.append(
            f"{index}. {row['product_name']}（{row['item_no']}，{row['category']}）："
            f"{money(row['sales_amount'])}，销量 {quantity(row['sales_quantity'])}，"
            f"涉及 {row['receipt_count']} 张小票"
        )
    return "\n".join(lines)


def category_contribution(database: str, bounds: Bounds) -> str:
    from sales_tools import query_category_contribution

    end_date = date.fromisoformat(bounds.latest_complete_date)
    result = query_category_contribution(
        database,
        start_date=(end_date - timedelta(days=6)).isoformat(),
        end_date=end_date.isoformat(),
        limit=10,
    )
    lines = [freshness_note(bounds), f"最近完整7天类别销售贡献（截止 {bounds.latest_complete_date}）："]
    for index, row in enumerate(result["rows"], start=1):
        lines.append(
            f"{index}. {row['category']}：{money(row['sales_amount'])}，"
            f"占 {row['contribution_percent']}%"
        )
    return "\n".join(lines)


def product_change(database: str, bounds: Bounds) -> str:
    from sales_tools import query_product_sales_change

    end_date = date.fromisoformat(bounds.latest_complete_date)
    result = query_product_sales_change(
        database,
        current_start_date=(end_date - timedelta(days=6)).isoformat(),
        current_end_date=end_date.isoformat(),
        previous_start_date=(end_date - timedelta(days=13)).isoformat(),
        previous_end_date=(end_date - timedelta(days=7)).isoformat(),
        limit=5,
        direction="both",
        category=None,
    )
    lines = [freshness_note(bounds), f"最近完整7天与此前7天的商品销量变化（截止 {bounds.latest_complete_date}）："]
    current_direction = None
    for row in result["rows"]:
        direction = "上升" if row["direction"] == "up" else "下降"
        if direction != current_direction:
            lines.append(f"{direction}最多：")
            current_direction = direction
        lines.append(
            f"- {row['product_name']}（{row['item_no']}）："
            f"{quantity(row['previous_quantity'])} → {quantity(row['current_quantity'])}，"
            f"变化 {quantity(row['quantity_delta'])}"
        )
    return "\n".join(lines)


def kpi_change(database: str, bounds: Bounds) -> str:
    from sales_tools import query_kpi_comparison

    end_date = date.fromisoformat(bounds.latest_complete_date)
    result = query_kpi_comparison(
        database,
        current_start_date=(end_date - timedelta(days=6)).isoformat(),
        current_end_date=end_date.isoformat(),
        previous_start_date=(end_date - timedelta(days=13)).isoformat(),
        previous_end_date=(end_date - timedelta(days=7)).isoformat(),
    )
    lines = [freshness_note(bounds), "客单价与订单量变化："]
    for row in result["rows"]:
        label = "最近完整7天" if row["period"] == "current" else "此前7天"
        avg_text = (
            "无可用小票" if not row["average_receipt_amount"]
            else money(row["average_receipt_amount"])
        )
        lines.append(
            f"- {label}（{row['start_date']} 至 {row['end_date']}）："
            f"小票 {row['receipt_count']} 张，客单价 {avg_text}，"
            f"销售额 {money(row['sales_amount'])}"
        )
    return "\n".join(lines)


def help_text() -> str:
    return "\n".join([
        "我目前可以回答：",
        "- 今天、昨天、本周销售额是多少？",
        "- 最近哪些商品卖得最好？",
        "- 哪些商品销量明显上升或下降？",
        "- 哪些类别贡献最多？",
        "- 客单价和订单量如何变化？",
    ])


def unsupported_text() -> str:
    return "\n".join([
        "这个问题目前超出首版经营问答范围，我不会用其他指标代替回答。",
        help_text(),
    ])


def answer(question: str, database: str = "supermarket_agent") -> str:
    intent = classify_intent(question)
    if intent == "help":
        return help_text()
    if intent == "unsupported":
        return unsupported_text()
    bounds = get_bounds(database)
    handlers = {
        "sales_summary": sales_summary,
        "top_products": top_products,
        "category_contribution": category_contribution,
        "product_change": product_change,
        "kpi_change": kpi_change,
    }
    return handlers[intent](database, bounds)


def main() -> int:
    parser = argparse.ArgumentParser(description="超市销售经营分析问答")
    parser.add_argument("question", nargs="+", help="中文经营问题")
    parser.add_argument("--database", default="supermarket_agent")
    args = parser.parse_args()
    try:
        print(answer(" ".join(args.question), args.database))
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
