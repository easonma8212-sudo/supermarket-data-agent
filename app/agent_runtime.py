"""Validated LLM planning followed by local read-only tool execution."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from decimal import Decimal
from typing import Any

from ai_planner import AIPlannerError, OpenAIShadowPlanner
from metric_query import EntityClarification, MetricPlanError, execute_metric_query
from sales_agent import Bounds, get_bounds
from sales_tools import ToolInputError, invoke_tool
from business_review import run_business_review


class AgentRuntimeError(RuntimeError):
    """Raised when a validated agent request cannot be completed safely."""


@dataclass(frozen=True)
class AgentReply:
    answer: str
    decision: str
    tool: str
    parameters: dict[str, Any] | None
    tool_executed: bool
    context: dict[str, Any] | None = None
    report: dict[str, Any] | None = None


ORDINALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}


def _select_candidate(
    answer: str, candidates: list[dict[str, str]]
) -> dict[str, str] | None:
    clean = answer.strip().replace(" ", "")
    index: int | None = None
    if clean.isdigit():
        index = int(clean)
    else:
        for chinese, number in ORDINALS.items():
            if clean in {f"第{chinese}个", f"第{chinese}项", chinese}:
                index = number
                break
        if clean.startswith("第"):
            digits = "".join(character for character in clean if character.isdigit())
            if digits:
                index = int(digits)
    if index is not None and 1 <= index <= len(candidates):
        return candidates[index - 1]
    exact = [
        candidate for candidate in candidates
        if clean in {candidate["item_no"], candidate["product_name"].replace(" ", "")}
    ]
    if len(exact) == 1:
        return exact[0]
    partial = [
        candidate for candidate in candidates
        if clean and clean in candidate["product_name"].replace(" ", "")
    ]
    return partial[0] if len(partial) == 1 else None


def _plan_context(
    question: str,
    plan: dict[str, Any],
    previous: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "last_user_question": question,
        "last_decision": plan["decision"],
    }
    if plan["decision"] == "execute":
        context["last_plan"] = {
            "tool": plan["tool"],
            "arguments": deepcopy(plan["arguments"]),
        }
    elif previous and previous.get("last_plan"):
        context["last_plan"] = deepcopy(previous["last_plan"])
    context.update(extra)
    return context


def _money(value: Any) -> str:
    return f"{Decimal(str(value or '0')):,.2f} 元"


def _quantity(value: Any) -> str:
    number = Decimal(str(value or "0"))
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _freshness(bounds: Bounds) -> str:
    return (
        f"数据覆盖 {bounds.earliest} 至 {bounds.latest_timestamp}。"
        f"最新数据日可能未完整；本次查询按计划使用指定日期范围。"
    )


def render_tool_result(result: dict[str, Any], bounds: Bounds) -> str:
    """Render certified tool output without asking the model to rewrite numbers."""
    tool = result["tool"]
    parameters = result["parameters"]
    if tool == 'run_business_review':
        return result['answer']
    if tool == "query_metrics":
        return render_metric_result(result, bounds)
    rows = result["rows"]
    lines = [_freshness(bounds)]
    if tool == "query_sales_summary":
        row = rows[0]
        lines.extend([
            f"销售汇总（{parameters['start_date']} 至 {parameters['end_date']}）：",
            f"- 销售额：{_money(row['sales_amount'])}",
            f"- 销量：{_quantity(row['sales_quantity'])}",
            f"- 小票数：{row['receipt_count']} 张",
            "- 客单价："
            + (_money(row["average_receipt_amount"]) if row["average_receipt_amount"] else "无可用小票"),
        ])
    elif tool == "query_top_products":
        sort_label = "销售额" if parameters["sort_by"] == "sales_amount" else "销量"
        category_label = f"，类别：{parameters['category']}" if parameters["category"] else ""
        lines.append(
            f"商品排行（{parameters['start_date']} 至 {parameters['end_date']}，"
            f"按{sort_label}{category_label}）："
        )
        for index, row in enumerate(rows, start=1):
            lines.append(
                f"{index}. {row['product_name']}（{row['item_no']}，{row['category']}）："
                f"销售额 {_money(row['sales_amount'])}，销量 {_quantity(row['sales_quantity'])}，"
                f"{row['receipt_count']} 张小票"
            )
    elif tool == "query_product_sales_change":
        lines.append(
            f"商品销量变化（{parameters['previous_start_date']} 至 {parameters['previous_end_date']} "
            f"对比 {parameters['current_start_date']} 至 {parameters['current_end_date']}）："
        )
        for row in rows:
            direction = "上升" if row["direction"] == "up" else "下降"
            lines.append(
                f"- {row['product_name']}（{row['item_no']}）："
                f"{_quantity(row['previous_quantity'])} → {_quantity(row['current_quantity'])}，"
                f"{direction} {_quantity(abs(Decimal(str(row['quantity_delta']))))}"
            )
    elif tool == "query_category_contribution":
        lines.append(f"类别销售贡献（{parameters['start_date']} 至 {parameters['end_date']}）：")
        for index, row in enumerate(rows, start=1):
            lines.append(
                f"{index}. {row['category']}：{_money(row['sales_amount'])}，"
                f"占 {row['contribution_percent']}%，销量 {_quantity(row['sales_quantity'])}"
            )
    elif tool == "query_kpi_comparison":
        lines.append("经营指标对比：")
        for row in rows:
            label = "当前期间" if row["period"] == "current" else "此前期间"
            average = (
                _money(row["average_receipt_amount"])
                if row["average_receipt_amount"] else "无可用小票"
            )
            lines.append(
                f"- {label}（{row['start_date']} 至 {row['end_date']}）："
                f"销售额 {_money(row['sales_amount'])}，销量 {_quantity(row['sales_quantity'])}，"
                f"小票 {row['receipt_count']} 张，客单价 {average}"
            )
    else:
        raise AgentRuntimeError(f"没有可用的结果模板：{tool}")

    if len(lines) == 2 and not rows:
        lines.append("所选范围内没有查询到销售记录。")
    return "\n".join(lines)


def _format_metric(metric: str, value: Any) -> str:
    if value in {None, ""}:
        return "无可用数据"
    if metric in {"sales_amount", "average_receipt_amount", "average_selling_price"}:
        return _money(value)
    if metric == "receipt_count":
        return f"{value} 张"
    return _quantity(value)


def render_metric_result(result: dict[str, Any], bounds: Bounds) -> str:
    parameters = result["parameters"]
    metric_labels = result["metric_labels"]
    metric_labels = dict(metric_labels)
    if parameters['filters']['product_query'] or parameters['filters']['category'] or parameters['dimension'] in {'product', 'category'}:
        metric_labels['average_receipt_amount'] = '每张相关小票的所选商品金额'
    resolved_product = parameters.get("resolved_product")
    filter_labels = []
    if resolved_product:
        filter_labels.append(
            f"商品：{resolved_product['product_name']}（{resolved_product['item_no']}）"
        )
    if parameters["filters"]["category"]:
        filter_labels.append(f"类别：{parameters['filters']['category']}")
    filter_text = "；" + "，".join(filter_labels) if filter_labels else ""
    lines = [_freshness(bounds), "NL2Metrics 查询结果" + filter_text + "："]
    dimension = parameters["dimension"]
    for period in result["periods"]:
        if len(result["periods"]) > 1:
            label = "当前期间" if period["label"] == "current" else "此前等长期间"
            lines.append(f"{label}（{period['start_date']} 至 {period['end_date']}）：")
        else:
            lines.append(f"期间：{period['start_date']} 至 {period['end_date']}")
        if not period["rows"]:
            lines.append("- 没有查询到销售记录")
            continue
        for index, row in enumerate(period["rows"], start=1):
            if dimension == "product":
                row_label = f"{row['product_name']}（{row['item_no']}）"
            elif dimension == "category":
                row_label = row["category"]
            elif dimension == "day":
                row_label = row["day"]
            else:
                row_label = "汇总"
            values = "，".join(
                f"{metric_labels[metric]} {_format_metric(metric, row.get(metric))}"
                for metric in parameters["metrics"]
            )
            prefix = f"{index}. " if dimension is not None else "- "
            lines.append(f"{prefix}{row_label}：{values}")
    return "\n".join(lines)


def answer_with_ai(
    question: str,
    database: str = "supermarket_agent",
    planner: OpenAIShadowPlanner | None = None,
    conversation_context: dict[str, Any] | None = None,
) -> AgentReply:
    """Plan with the LLM, validate locally, execute one approved tool, and render locally."""
    try:
        bounds = get_bounds(database)
        pending = (conversation_context or {}).get("pending_entity")
        selected = None
        if pending and pending.get("candidates"):
            selected = _select_candidate(question, pending["candidates"])
        if selected:
            arguments = deepcopy(pending["arguments"])
            arguments["filters"]["product_query"] = selected["item_no"]
            plan = {
                "decision": "execute",
                "tool": "query_metrics",
                "arguments": arguments,
            }
        else:
            active_planner = planner or OpenAIShadowPlanner.from_environment()
            plan = active_planner.plan(question, bounds, conversation_context)
    except AIPlannerError as exc:
        raise AgentRuntimeError(str(exc)) from exc

    decision = plan["decision"]
    tool = plan["tool"]
    arguments = plan["arguments"]
    if decision == "clarify":
        return AgentReply(
            answer=arguments["question"],
            decision=decision,
            tool=tool,
            parameters=None,
            tool_executed=False,
            context=_plan_context(
                question,
                plan,
                conversation_context,
                assistant_clarification=arguments["question"],
            ),
        )
    if decision == "refuse":
        return AgentReply(
            answer=(
                "这个问题目前没有可用的认证数据或已批准的只读工具，因此不会执行查询。\n"
                "当前支持销售汇总、商品排行、商品销量变化、类别贡献以及客单价和小票数对比。"
            ),
            decision=decision,
            tool=tool,
            parameters=None,
            tool_executed=False,
            context=conversation_context,
        )
    if decision != "execute":
        raise AgentRuntimeError("AI 返回了不支持的决策")

    try:
        if tool == 'run_business_review':
            result = run_business_review(database, **arguments)
        elif tool == "query_metrics":
            result = execute_metric_query(database, **arguments)
        else:
            result = invoke_tool(tool, arguments, database)
    except EntityClarification as exc:
        return AgentReply(
            answer=exc.question,
            decision="clarify",
            tool="query_metrics",
            parameters=None,
            tool_executed=False,
            context=_plan_context(
                question,
                plan,
                conversation_context,
                assistant_clarification=exc.question,
                pending_entity={
                    "tool": "query_metrics",
                    "arguments": deepcopy(arguments),
                    "candidates": deepcopy(exc.candidates),
                },
            ),
        )
    except (MetricPlanError, ToolInputError, TypeError, ValueError) as exc:
        raise AgentRuntimeError(f"AI 参数未通过本机安全校验：{exc}") from exc
    return AgentReply(
        answer=render_tool_result(result, bounds),
        decision=decision,
        tool=tool,
        parameters=result["parameters"],
        tool_executed=True,
        context=_plan_context(question, plan, conversation_context),
        report=result.get('report'),
    )
