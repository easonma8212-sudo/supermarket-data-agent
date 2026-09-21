CREATE TABLE IF NOT EXISTS config.metric_definition (
    metric_id text PRIMARY KEY,
    label text NOT NULL,
    definition text NOT NULL,
    unit text NOT NULL,
    default_grain text NOT NULL,
    active boolean NOT NULL DEFAULT true
);

INSERT INTO config.metric_definition (
    metric_id, label, definition, unit, default_grain
)
VALUES
    ('sales_amount', '销售额', '销售明细中实际金额之和。', '元', '日'),
    ('sales_quantity', '销售数量', '销售明细中数量之和，保留称重商品小数。', '商品单位', '日'),
    ('receipt_count', '小票数', '按销售单号去重后的数量。', '张', '日'),
    ('average_receipt_amount', '客单价', '销售额除以同期去重小票数。', '元/张', '日'),
    ('category_contribution', '类别销售贡献', '类别销售额除以同期全部类别销售额。', '%', '期间'),
    ('product_sales_change', '商品销量变化', '最近完整7天销售数量与此前完整7天销售数量之差。', '商品单位', '7天')
ON CONFLICT (metric_id) DO UPDATE SET
    label = EXCLUDED.label,
    definition = EXCLUDED.definition,
    unit = EXCLUDED.unit,
    default_grain = EXCLUDED.default_grain,
    active = true;

CREATE OR REPLACE VIEW analytics.data_freshness AS
SELECT
    min(sold_at) AS earliest_sale_timestamp,
    max(sold_at) AS latest_sale_timestamp,
    max(sold_at)::date AS latest_data_date,
    max(sold_at)::date - 1 AS latest_complete_date,
    count(*) AS sales_line_count,
    '保守口径：最新数据日期视为可能未完整；趋势分析截止到前一日。'::text AS completeness_rule
FROM raw.sales_detail;

COMMENT ON VIEW analytics.data_freshness IS
    '销售数据覆盖和保守完整日口径；回答中必须向用户展示数据截止时间。';
