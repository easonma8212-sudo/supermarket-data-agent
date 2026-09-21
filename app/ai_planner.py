"""OpenAI Responses API planner for read-only shadow-mode tool selection."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import ssl
import time
from typing import Any
from http.client import RemoteDisconnected
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sales_agent import Bounds
from metric_query import METRIC_TOOL_SCHEMA
from sales_tools import TOOL_SCHEMAS
from business_review import SKILL_SCHEMA, skill_instructions

try:
    import certifi
except ImportError:  # pragma: no cover - system CA bundle remains the fallback
    certifi = None


RESPONSES_URL = "https://api.openai.com/v1/responses"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENV_FILE = PROJECT_ROOT / ".env"
LOCAL_ENV_KEYS = {
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "SUPERMARKET_DATABASE",
    "AI_SHADOW_MODE",
    "AI_WEB_MODE",
}


class AIPlannerError(RuntimeError):
    """Raised when the shadow planner cannot produce a valid decision."""


def build_ssl_context() -> ssl.SSLContext:
    """Build a verified TLS context, preferring certifi on Python installations without a CA bundle."""
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def load_local_environment(path: Path = LOCAL_ENV_FILE) -> None:
    """Load the small whitelisted local config without adding a dependency."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in LOCAL_ENV_KEYS or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


POLICY_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "request_clarification",
        "description": "Only when a missing critical detail has multiple materially different answers and no safe default.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "One concise clarification question in Chinese."},
                "reason": {"type": "string", "description": "Why execution would otherwise be materially ambiguous."},
            },
            "required": ["question", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "refuse_request",
        "description": "Use when required data or an approved read-only capability is unavailable.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "A concise boundary explanation in Chinese."},
                "reason": {"type": "string", "description": "The unavailable data or capability."},
            },
            "required": ["message", "reason"],
            "additionalProperties": False,
        },
    },
]


INSTRUCTIONS = """你是单店超市的只读经营分析规划器。你的唯一任务是选择一个工具并填写参数，不能生成 SQL，也不能计算或编造经营数字。

优先使用通用 query_metrics 表达“指标 + 维度 + 筛选 + 时间 + 排序 + 对比”：
- 用户指定具体商品名称或编号时，必须把原始名称放入 filters.product_query，使用 query_metrics，不能忽略商品筛选。
- “按天/每天/趋势”使用 dimension=day；商品排行使用 product；类别排行使用 category；只看汇总使用 null。
- 销售额、销量、小票数、客单价、平均售价分别映射到 sales_amount、sales_quantity、receipt_count、average_receipt_amount、average_selling_price。
- 只有用户明确要求“对比、变化、增长、下降、和上期/以前比”时才使用 comparison=previous_period；普通的“卖得怎么样”“销售情况”必须使用 none，不能擅自增加对比。
- 没有排序需求时 order_by=null、order_direction=desc。没有分组时 limit=1。
- dimension=day 且用户没有要求“最高/最低的前几天”时，limit 填写请求范围的天数（最多50），以返回完整日趋势。
- 类别名称去掉口语中的“类”，例如“饮料类”写成“饮料”。
- query_product_sales_change 暂时只用于“哪些商品上升或下降最多”这类需要按跨期差值排行的问题；类别贡献占比继续使用 query_category_contribution。其他可表达问题优先 query_metrics。

多轮上下文规则：输入中如果包含 conversation_context，当前问题可能是对上一轮的补充。应继承上一轮已经明确、而当前没有修改的商品、指标、维度和时间条件；只应用用户本轮明确提出的变化。例如上一轮查鸡蛋后说“改成最近7天每天看”，应保留鸡蛋并改为最近7天、dimension=day。不能把上下文中的数据库结果当作输入，因为上下文只包含语义计划，不包含查询结果。

执行前硬门禁：比较类请求必须明确“比较什么指标”。如果用户只说“和以前比怎么样”“对比一下”而没有给出销售额、销量、小票数、客单价、商品或类别等比较对象，必须调用 request_clarification，绝对不能自行选择 query_kpi_comparison 或其他查询工具。

决策顺序：
1. 问题可以映射到经营分析工具时，直接调用工具。
2. 缺少参数但有安全默认值时，直接使用默认值：畅销商品与类别贡献默认最近完整7天、前10名；畅销默认按销售额；商品变化默认最近完整7天对比此前7天、上升下降各前5名。
3. 最新数据日可能不完整。除非用户明确询问今天或最新日，否则趋势和排行截止最新完整日。
4. “最近两周对比”表示以最新完整日为截止的最近完整7天，对比紧邻此前7天；不要跳过最新完整周，也不要整体向前移动一周。
5. 只有缺失信息会产生明显不同答案且没有安全默认值时，调用 request_clarification；每次只问一个最关键问题。像“和以前比怎么样”这类同时缺少比较指标和期间的问题必须澄清，不能自行假设为 KPI 对比。
6. 数据或能力不存在时调用 refuse_request。利润、毛利、库存异常、补货、预测和因果判断目前不支持。

日期必须转换成 YYYY-MM-DD。相对日期以提供的数据日期为准，而不是模型当前日期。必须且只能调用一个工具。"""


def build_request_payload(
    question: str,
    model: str,
    bounds: Bounds,
    conversation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = {
        "question": question,
        "data_context": asdict(bounds),
    }
    if conversation_context:
        context["conversation_context"] = conversation_context
    return {
        "model": model,
        "store": False,
        "instructions": INSTRUCTIONS + '\n经营分析能力优先路由：全店经营概览、最近生意怎么样、销售变化体现在哪里，优先选择 run_business_review；单个数字和指定商品继续使用 query_metrics。用户追问此分析的时间时继承 Skill。因果问题可提供变化拆解但不可证明原因。\n' + skill_instructions(),
        "input": json.dumps(context, ensure_ascii=False),
        "tools": [SKILL_SCHEMA, METRIC_TOOL_SCHEMA, *TOOL_SCHEMAS, *POLICY_TOOLS],
        "tool_choice": "required",
        "parallel_tool_calls": False,
        "temperature": 0,
    }


def parse_planner_response(payload: dict[str, Any]) -> dict[str, Any]:
    calls = [item for item in payload.get("output", []) if item.get("type") == "function_call"]
    if len(calls) != 1:
        raise AIPlannerError("AI 规划器必须返回且只能返回一个工具调用")
    call = calls[0]
    try:
        arguments = json.loads(call["arguments"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AIPlannerError("AI 规划器返回了无效工具参数") from exc
    name = call.get("name")
    if name == "request_clarification":
        decision = "clarify"
    elif name == "refuse_request":
        decision = "refuse"
    elif name in {SKILL_SCHEMA["name"], METRIC_TOOL_SCHEMA["name"], *(tool["name"] for tool in TOOL_SCHEMAS)}:
        decision = "execute"
    else:
        raise AIPlannerError(f"AI 规划器返回了未知工具：{name}")
    return {
        "decision": decision,
        "tool": name,
        "arguments": arguments,
        "response_id": payload.get("id"),
        "model": payload.get("model"),
    }


class OpenAIShadowPlanner:
    def __init__(self, api_key: str, model: str, timeout_seconds: int = 30):
        if not api_key:
            raise AIPlannerError("尚未配置 OPENAI_API_KEY")
        if not model:
            raise AIPlannerError("尚未配置 OPENAI_MODEL")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> "OpenAIShadowPlanner":
        load_local_environment()
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=os.environ.get("OPENAI_MODEL", ""),
        )

    def plan(
        self,
        question: str,
        bounds: Bounds,
        conversation_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not question.strip():
            raise AIPlannerError("问题不能为空")
        body = json.dumps(
            build_request_payload(
                question.strip(), self.model, bounds, conversation_context
            ),
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            RESPONSES_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        payload: dict[str, Any] | None = None
        for attempt in range(3):
            try:
                with urlopen(
                    request,
                    timeout=self.timeout_seconds,
                    context=build_ssl_context(),
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                retryable = exc.code in {408, 409, 429, 500, 502, 503, 504}
                if retryable and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise AIPlannerError(f"OpenAI API 请求失败（HTTP {exc.code}）") from exc
            except (URLError, TimeoutError, RemoteDisconnected, ConnectionError) as exc:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise AIPlannerError("OpenAI API 暂时无法连接（重试后仍失败）") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AIPlannerError("OpenAI API 返回了无法解析的响应") from exc
        if payload is None:  # pragma: no cover - all exhausted paths raise above
            raise AIPlannerError("OpenAI API 未返回响应")
        return parse_planner_response(payload)
