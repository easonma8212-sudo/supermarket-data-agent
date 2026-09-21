CREATE SCHEMA IF NOT EXISTS config;

CREATE TABLE IF NOT EXISTS config.replenishment_policy (
    supply_mode text PRIMARY KEY,
    label text NOT NULL,
    default_coverage_days integer CHECK (default_coverage_days > 0),
    long_coverage_days integer CHECK (long_coverage_days > 0),
    description text NOT NULL
);

INSERT INTO config.replenishment_policy (
    supply_mode, label, default_coverage_days, long_coverage_days, description
)
VALUES
    ('local_supplier', '本地供应商补货', 7, NULL,
     '供应商可随时补货并定期巡店；首版显示未来 7 天预计销售需求。'),
    ('self_purchase', '自行采购', 30, 60,
     '采购约需 1 天但采购周期不固定；同时显示未来 30 天和 60 天需求情景。'),
    ('manual_review', '待人工分类', NULL, NULL,
     '商品分类不足以判断采购渠道，首版不计算补货覆盖量。')
ON CONFLICT (supply_mode) DO UPDATE SET
    label = EXCLUDED.label,
    default_coverage_days = EXCLUDED.default_coverage_days,
    long_coverage_days = EXCLUDED.long_coverage_days,
    description = EXCLUDED.description;

CREATE TABLE IF NOT EXISTS config.category_supply_mode (
    category text PRIMARY KEY,
    supply_mode text NOT NULL REFERENCES config.replenishment_policy(supply_mode),
    note text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO config.category_supply_mode (category, supply_mode, note)
VALUES
    ('饮料', 'local_supplier', '用户确认饮料由本地供应商随时补货。'),
    ('酒水', 'local_supplier', '暂归入饮料类本地供应。'),
    ('农夫水', 'local_supplier', '饮用水，暂归入饮料类本地供应。'),
    ('礼盒奶', 'local_supplier', '奶饮品，暂归入饮料类本地供应。'),
    ('零食', 'local_supplier', '用户确认零食由本地供应商随时补货。'),
    ('日化', 'self_purchase', '日用品，暂按自行采购处理。'),
    ('家居百货', 'self_purchase', '日用品，暂按自行采购处理。'),
    ('文具类', 'self_purchase', '日用品，暂按自行采购处理。')
ON CONFLICT (category) DO UPDATE SET
    supply_mode = EXCLUDED.supply_mode,
    note = EXCLUDED.note,
    updated_at = now();

CREATE OR REPLACE VIEW analytics.replenishment_attention AS
WITH bounds AS (
    SELECT max(sold_at)::date AS as_of_date
    FROM raw.sales_detail
),
sales AS (
    SELECT
        s.item_no,
        max(s.product_name) AS product_name,
        max(s.category) AS category,
        max(s.brand) AS brand,
        max(s.sold_at)::date AS last_sale_date,
        sum(s.quantity) FILTER (
            WHERE s.sold_at::date BETWEEN b.as_of_date - 27 AND b.as_of_date
        ) AS quantity_28d,
        sum(s.actual_amount) FILTER (
            WHERE s.sold_at::date BETWEEN b.as_of_date - 27 AND b.as_of_date
        ) AS sales_amount_28d,
        count(DISTINCT s.sold_at::date) FILTER (
            WHERE s.sold_at::date BETWEEN b.as_of_date - 27 AND b.as_of_date
        ) AS sales_days_28d,
        sum(s.quantity) FILTER (
            WHERE s.sold_at::date BETWEEN b.as_of_date - 55 AND b.as_of_date - 28
        ) AS quantity_previous_28d
    FROM raw.sales_detail s
    CROSS JOIN bounds b
    GROUP BY s.item_no
),
classified AS (
    SELECT
        b.as_of_date,
        s.*,
        coalesce(m.supply_mode, 'manual_review') AS supply_mode
    FROM sales s
    CROSS JOIN bounds b
    LEFT JOIN config.category_supply_mode m ON m.category = s.category
)
SELECT
    c.as_of_date,
    c.item_no,
    c.product_name,
    c.category,
    c.brand,
    c.supply_mode,
    p.label AS supply_mode_label,
    c.last_sale_date,
    c.as_of_date - c.last_sale_date AS days_since_last_sale,
    coalesce(c.quantity_28d, 0) AS quantity_28d,
    round(coalesce(c.quantity_28d, 0) / 28.0, 3) AS average_daily_quantity_28d,
    coalesce(c.sales_amount_28d, 0) AS sales_amount_28d,
    c.sales_days_28d,
    coalesce(c.quantity_previous_28d, 0) AS quantity_previous_28d,
    CASE
        WHEN coalesce(c.quantity_previous_28d, 0) <= 0 THEN NULL
        ELSE round(c.quantity_28d / c.quantity_previous_28d, 3)
    END AS quantity_trend_ratio,
    p.default_coverage_days,
    CASE
        WHEN p.default_coverage_days IS NULL THEN NULL
        ELSE ceil(greatest(coalesce(c.quantity_28d, 0), 0) / 28.0 * p.default_coverage_days)
    END AS estimated_coverage_units,
    p.long_coverage_days,
    CASE
        WHEN p.long_coverage_days IS NULL THEN NULL
        ELSE ceil(greatest(coalesce(c.quantity_28d, 0), 0) / 28.0 * p.long_coverage_days)
    END AS estimated_long_coverage_units,
    CASE
        WHEN c.supply_mode = 'local_supplier' THEN '供应商巡店时核对陈列和缺货情况'
        WHEN c.supply_mode = 'self_purchase' THEN '纳入下一次自行采购检查'
        ELSE '先确认商品类别和采购渠道'
    END AS suggested_action
FROM classified c
JOIN config.replenishment_policy p ON p.supply_mode = c.supply_mode;

COMMENT ON VIEW analytics.replenishment_attention IS
    '基于最近 28 天销售速度估算覆盖期需求；不是订单量，不使用 POS 库存作为真实库存。';
