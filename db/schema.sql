CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS ops.import_run (
    import_id uuid PRIMARY KEY,
    source_kind text NOT NULL CHECK (source_kind IN ('sales_detail', 'inventory_snapshot')),
    source_filename text NOT NULL,
    source_sha256 text NOT NULL,
    row_count integer NOT NULL CHECK (row_count >= 0),
    imported_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_kind, source_sha256)
);

CREATE TABLE IF NOT EXISTS raw.sales_detail (
    import_id uuid NOT NULL REFERENCES ops.import_run(import_id),
    source_row integer NOT NULL,
    warehouse text,
    receipt_no text,
    document_type text,
    sold_at timestamp,
    sale_method text,
    item_no text,
    barcode text,
    scanned_code text,
    product_name text,
    specification text,
    category text,
    brand text,
    quantity numeric(18,3),
    original_unit_price numeric(18,4),
    actual_unit_price numeric(18,4),
    discount_amount numeric(18,2),
    discount_rate numeric(9,6),
    original_amount numeric(18,2),
    actual_amount numeric(18,2),
    promotion_type text,
    points numeric(18,2),
    salesperson text,
    cashier text,
    terminal_no text,
    shift_no text,
    type_note text,
    batch_no text,
    expiry_date date,
    production_date date,
    PRIMARY KEY (import_id, source_row)
);

CREATE TABLE IF NOT EXISTS raw.inventory_snapshot (
    import_id uuid NOT NULL REFERENCES ops.import_run(import_id),
    source_row integer NOT NULL,
    snapshot_date date NOT NULL,
    warehouse text,
    item_no text,
    barcode text,
    product_name text,
    specification text,
    unit text,
    inventory_quantity numeric(18,3),
    inventory_unit_cost numeric(18,4),
    inventory_cost_amount numeric(18,2),
    inventory_upper_limit numeric(18,3),
    inventory_lower_limit numeric(18,3),
    category text,
    brand text,
    PRIMARY KEY (import_id, source_row)
);

CREATE INDEX IF NOT EXISTS sales_detail_sold_at_idx
    ON raw.sales_detail (sold_at);
CREATE INDEX IF NOT EXISTS sales_detail_item_no_idx
    ON raw.sales_detail (item_no);
CREATE INDEX IF NOT EXISTS inventory_snapshot_item_date_idx
    ON raw.inventory_snapshot (item_no, snapshot_date DESC);

CREATE OR REPLACE VIEW analytics.daily_sales AS
SELECT
    sold_at::date AS sales_date,
    count(DISTINCT receipt_no) AS receipt_count,
    count(*) AS line_count,
    sum(quantity) AS quantity,
    sum(actual_amount) AS sales_amount,
    CASE
        WHEN count(DISTINCT receipt_no) = 0 THEN NULL
        ELSE round(sum(actual_amount) / count(DISTINCT receipt_no), 2)
    END AS average_receipt_amount
FROM raw.sales_detail
GROUP BY sold_at::date;

CREATE OR REPLACE VIEW analytics.product_sales AS
SELECT
    item_no,
    max(product_name) AS product_name,
    max(category) AS category,
    max(brand) AS brand,
    min(sold_at::date) AS first_sale_date,
    max(sold_at::date) AS last_sale_date,
    count(DISTINCT receipt_no) AS receipt_count,
    sum(quantity) AS quantity,
    sum(actual_amount) AS sales_amount,
    CASE
        WHEN sum(quantity) = 0 THEN NULL
        ELSE round(sum(actual_amount) / sum(quantity), 4)
    END AS average_selling_price
FROM raw.sales_detail
GROUP BY item_no;

COMMENT ON SCHEMA raw IS '从 POS 导出文件导入的数据副本；不写回 POS。';
COMMENT ON SCHEMA analytics IS '只读经营分析视图。';
COMMENT ON VIEW analytics.product_sales IS
    '按当前已导入销售范围汇总；库存数量不作为补货算法的可靠输入。';

\ir migrations/003_replenishment_attention.sql
\ir migrations/004_sales_query_agent.sql
