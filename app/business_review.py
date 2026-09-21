"""A bounded business-review skill: local evidence, reconciled changes, no causal claims."""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import json

from sales_agent import get_bounds, run_sql
from sales_tools import ToolInputError

SKILL_PATH = Path(__file__).resolve().parents[1] / 'skills/business-review/SKILL.md'
SKILL_SCHEMA = {
    'type': 'function', 'name': 'run_business_review', 'strict': True,
    'description': '经营分析 Skill：全店经营概览与销售变化拆解，比较此前等长期间，分析销售额、小票数、客单价及类别和商品金额变化贡献。不能用于指定商品或类别筛选。',
    'parameters': {'type': 'object', 'properties': {
        'start_date': {'type': 'string'}, 'end_date': {'type': 'string'}
    }, 'required': ['start_date', 'end_date'], 'additionalProperties': False}
}

def skill_instructions():
    return SKILL_PATH.read_text(encoding='utf-8').split('---', 2)[2].strip()

def period_parameters(start_date, end_date, bounds):
    try:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except (ValueError, TypeError) as exc:
        raise ToolInputError('经营分析日期必须是 YYYY-MM-DD') from exc
    days = (end - start).days + 1
    previous = start - timedelta(days=days)
    if not 1 <= days <= 31:
        raise ToolInputError('经营分析当前期间须为1至31天')
    if previous < date.fromisoformat(bounds.earliest) or end > date.fromisoformat(bounds.latest_complete_date):
        raise ToolInputError('两期分析必须在已导入的完整日期范围内')
    return {'start': start.isoformat(), 'end': end.isoformat(),
            'previous_start': previous.isoformat(), 'previous_end': (start-timedelta(days=1)).isoformat()}

def _query(database, sql, parameters):
    # Each bounded SELECT uses a read-only transaction and a database timeout.
    rows = run_sql(database, "BEGIN READ ONLY; SET LOCAL statement_timeout='10000ms';\n" + sql + '\nCOMMIT;', parameters)
    return [json.loads(row[0], parse_float=Decimal) for row in rows]

def rate(current, previous):
    return None if previous <= 0 else (current - previous) / previous * 100

def change_text(current, previous, unit='元'):
    current, previous = Decimal(str(current)), Decimal(str(previous))
    percent = rate(current, previous)
    suffix = '此前值非正，不计算增长率' if percent is None else f'{percent:+.1f}%'
    return f'{previous:,.2f} → {current:,.2f} {unit}，变化 {current-previous:+,.2f} {unit}（{suffix}）'

def render_review(summary, breakdowns, parameters, bounds):
    by_period = {row['period']: row for row in summary}
    current, previous = by_period['current'], by_period['previous']
    delta = Decimal(str(current['amount'])) - Decimal(str(previous['amount']))
    lines = ['经营概览与变化拆解',
             f"当前：{parameters['start']} 至 {parameters['end']}；此前：{parameters['previous_start']} 至 {parameters['previous_end']}",
             f'数据截止 {bounds.latest_timestamp}；按最新完整日 {bounds.latest_complete_date} 校验。']
    if any(row['invalid'] for row in summary):
        return '\n'.join(lines + ['数据检查未通过：存在缺失金额、商品编号或小票编号。请核实后再生成经营结论。'])
    for rows in breakdowns.values():
        if sum((Decimal(str(r['current'])) - Decimal(str(r['previous'])) for r in rows), Decimal(0)) != delta:
            raise ToolInputError('拆分与全店金额变化未对账，已停止输出分析')
    days = (date.fromisoformat(parameters['end'])-date.fromisoformat(parameters['start'])).days+1
    for row in summary:
        if row['days'] < days:
            label = '当前期间' if row['period']=='current' else '此前期间'
            lines.append(f"数据提示：{label}仅 {row['days']}/{days} 天有记录；请核实停业或漏导，不能仅凭日期覆盖认定完整。")
    direction = '增加' if delta > 0 else '减少' if delta < 0 else '持平'
    lines.append(f'经营结论：本期销售额较此前{direction}，金额差额 {delta:+,.2f} 元 [E1]。类别和商品拆分见 [E2]、[E3]。')
    lines += ['[E1] 核心指标（此前 → 当前）',
              '销售额：' + change_text(current['amount'], previous['amount']),
              '小票数：' + change_text(current['receipts'], previous['receipts'], '张')]
    if current['receipts'] and previous['receipts']:
        a = Decimal(str(current['amount'])) / current['receipts']
        b = Decimal(str(previous['amount'])) / previous['receipts']
        lines.append('全店客单价：' + change_text(a, b))
    else:
        lines.append('客单价：某期间无有效小票，不能比较。')
    for evidence, dimension in [('E2', '类别'), ('E3', '商品')]:
        rows = breakdowns[dimension]
        changes = [(r, Decimal(str(r['current'])) - Decimal(str(r['previous']))) for r in rows]
        lines.append(f'[{evidence}] {dimension}金额变化（全量对账后，涨跌各取前3项）')
        for label, selected in [('增加', sorted([x for x in changes if x[1]>0], key=lambda x: (-x[1], str(x[0]['id'])))[:3]),
                                ('减少', sorted([x for x in changes if x[1]<0], key=lambda x: (x[1], str(x[0]['id'])))[:3])]:
            if not selected:
                lines.append(f'- 没有{label}项。')
            for row, difference in selected:
                lines.append(f"- {row['name']}（{row['id']}）：{change_text(row['current'], row['previous'])}")
        shown = sum((d for _,d in sorted([x for x in changes if x[1]>0], key=lambda x:-x[1])[:3]),Decimal(0)) + sum((d for _,d in sorted([x for x in changes if x[1]<0],key=lambda x:x[1])[:3]),Decimal(0))
        lines.append(f'其余{dimension}净变化 {delta-shown:+,.2f} 元；全部{dimension}净变化 {delta:+,.2f} 元，与全店一致。')
    lines += ['分析边界：类别与商品是同一金额变化的两种拆分，不能相加；以上说明变化体现在哪里，不证明促销、缺货、天气或客流等原因。',
              '建议核实：优先检查 [E3] 中变化较大的商品是否存在价格、促销、录入或供货变化，再判断经营原因。小票数不等于顾客人数。']
    return '\n'.join(lines)

def run_business_review(database, *, start_date, end_date):
    bounds = get_bounds(database)
    parameters = period_parameters(start_date, end_date, bounds)
    summary = _query(database, """
WITH periods AS (
 SELECT 'current' AS period, CAST(:'start' AS date) AS s, CAST(:'end' AS date) AS e
 UNION ALL SELECT 'previous', CAST(:'previous_start' AS date), CAST(:'previous_end' AS date)
), result AS (
 SELECT p.period, coalesce(sum(s.actual_amount),0) AS amount,
 count(DISTINCT nullif(s.receipt_no,'')) AS receipts,
 count(DISTINCT s.sold_at::date) AS days,
 count(*) FILTER (WHERE s.import_id IS NOT NULL AND
 (s.actual_amount IS NULL OR nullif(s.item_no,'') IS NULL OR nullif(s.receipt_no,'') IS NULL)) AS invalid
 FROM periods p LEFT JOIN raw.sales_detail s ON s.sold_at::date BETWEEN p.s AND p.e
 GROUP BY p.period
) SELECT row_to_json(result) FROM result;
""", parameters)
    breakdowns = {}
    for dimension, key, name in [('类别', "coalesce(category,'未分类')", "coalesce(category,'未分类')"),
                                  ('商品', 'item_no', 'max(product_name)')]:
        sql = f"""
WITH result AS (
 SELECT {key} AS id, {name} AS name,
 coalesce(sum(actual_amount) FILTER (WHERE sold_at::date BETWEEN CAST(:'start' AS date) AND CAST(:'end' AS date)),0) AS current,
 coalesce(sum(actual_amount) FILTER (WHERE sold_at::date BETWEEN CAST(:'previous_start' AS date) AND CAST(:'previous_end' AS date)),0) AS previous
 FROM raw.sales_detail
 WHERE sold_at::date BETWEEN CAST(:'previous_start' AS date) AND CAST(:'end' AS date)
 GROUP BY {key}
) SELECT row_to_json(result) FROM result;
"""
        breakdowns[dimension] = _query(database, sql, parameters)
    answer = render_review(summary, breakdowns, parameters, bounds)
    report = None
    if not any(row['invalid'] for row in summary):
        # Reconciliation has already succeeded in render_review. Decimal values
        # travel as strings, preserving the same accounting evidence as the text.
        report = json.loads(json.dumps({
            'title': '经营分析报告', 'periods': parameters,
            'freshness': bounds.latest_timestamp,
            'summary': summary, 'breakdowns': breakdowns,
            'warnings': [line for line in answer.splitlines() if line.startswith('数据提示：')],
        }, default=str, ensure_ascii=False))
    return {'tool': 'run_business_review', 'parameters': {'start_date': start_date, 'end_date': end_date},
            'answer': answer, 'report': report}
