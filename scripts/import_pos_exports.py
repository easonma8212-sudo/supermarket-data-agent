"""Normalize Yun POS exports and load them into local PostgreSQL.

The source files are GB18030-encoded, tab-delimited exports with an HTML
prefix in the first header and a trailing empty column. Customer-identifying
fields are not accepted by this importer.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import uuid


SALES_FIELDS = [
    "warehouse", "receipt_no", "document_type", "sold_at", "sale_method",
    "item_no", "barcode", "scanned_code", "product_name", "specification",
    "category", "brand", "quantity", "original_unit_price",
    "actual_unit_price", "discount_amount", "discount_rate",
    "original_amount", "actual_amount", "promotion_type", "points",
    "salesperson", "cashier", "terminal_no", "shift_no", "type_note",
    "batch_no", "expiry_date", "production_date",
]

INVENTORY_FIELDS = [
    "warehouse", "item_no", "barcode", "product_name", "specification",
    "unit", "inventory_quantity", "inventory_unit_cost",
    "inventory_cost_amount", "inventory_upper_limit", "inventory_lower_limit",
    "category", "brand",
]

FORBIDDEN_HEADERS = {"会员卡号", "会员名称", "备注"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_export(path: Path, expected_fields: int) -> tuple[list[str], list[list[str]]]:
    with path.open("r", encoding="gb18030", newline="") as source:
        rows = list(csv.reader(source, delimiter="\t"))
    if not rows:
        raise ValueError(f"空文件：{path}")
    headers = rows[0]
    if FORBIDDEN_HEADERS.intersection(headers):
        raise ValueError(f"文件包含首版不允许导入的个人信息字段：{path}")
    data = []
    for row_number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        if row and row[-1] == "":
            row = row[:-1]
        if len(row) != expected_fields:
            raise ValueError(
                f"{path.name} 第 {row_number} 行字段数为 {len(row)}，"
                f"预期 {expected_fields}"
            )
        data.append([cell.strip() for cell in row])
    return headers, data


def decimal_value(value: str, *, percent: bool = False) -> str:
    if value == "":
        return ""
    raw = value.removesuffix("%").removesuffix("％")
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"无法解析数值：{value}") from exc
    if percent:
        number /= Decimal("100")
    return format(number, "f")


def optional_date(value: str) -> str:
    if not value:
        return ""
    for date_format in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"无法解析日期：{value}")


def datetime_value(value: str) -> str:
    for date_format in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(value, date_format).isoformat(sep=" ")
        except ValueError:
            pass
    raise ValueError(f"无法解析日期时间：{value}")


def csv_buffer(header: list[str], rows: list[list[str]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows(rows)
    return output.getvalue()


def run_psql(database: str, sql: str) -> None:
    subprocess.run(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", database],
        input=sql,
        text=True,
        check=True,
    )


def import_sales(database: str, path: Path) -> tuple[uuid.UUID, int]:
    _, rows = read_export(path, len(SALES_FIELDS))
    digest = file_sha256(path)
    import_id = uuid.uuid5(uuid.NAMESPACE_URL, f"sales_detail:{digest}")
    normalized = []
    for source_row, row in enumerate(rows, start=2):
        row[3] = datetime_value(row[3])
        for index in (12, 13, 14, 15, 17, 18, 20):
            row[index] = decimal_value(row[index])
        row[16] = decimal_value(row[16], percent=True)
        row[27] = optional_date(row[27])
        row[28] = optional_date(row[28])
        normalized.append([str(import_id), source_row, *row])

    columns = ["import_id", "source_row", *SALES_FIELDS]
    payload = csv_buffer(columns, normalized)
    metadata = (
        "INSERT INTO ops.import_run "
        "(import_id, source_kind, source_filename, source_sha256, row_count) "
        f"VALUES ('{import_id}', 'sales_detail', "
        f"'{path.name.replace(chr(39), chr(39) * 2)}', '{digest}', {len(rows)}) "
        "ON CONFLICT (source_kind, source_sha256) DO NOTHING;"
    )
    sql = f"""BEGIN;
{metadata}
DELETE FROM raw.sales_detail WHERE import_id = '{import_id}';
COPY raw.sales_detail ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv, HEADER true);
{payload}\\.
COMMIT;
"""
    run_psql(database, sql)
    return import_id, len(rows)


def import_inventory(database: str, path: Path, snapshot_date: str) -> tuple[uuid.UUID, int]:
    _, rows = read_export(path, len(INVENTORY_FIELDS))
    snapshot = datetime.strptime(snapshot_date, "%Y-%m-%d").date().isoformat()
    digest = file_sha256(path)
    import_id = uuid.uuid5(uuid.NAMESPACE_URL, f"inventory_snapshot:{digest}")
    normalized = []
    for source_row, row in enumerate(rows, start=2):
        for index in (6, 7, 8, 9, 10):
            row[index] = decimal_value(row[index])
        normalized.append([str(import_id), source_row, snapshot, *row])

    columns = ["import_id", "source_row", "snapshot_date", *INVENTORY_FIELDS]
    payload = csv_buffer(columns, normalized)
    metadata = (
        "INSERT INTO ops.import_run "
        "(import_id, source_kind, source_filename, source_sha256, row_count) "
        f"VALUES ('{import_id}', 'inventory_snapshot', "
        f"'{path.name.replace(chr(39), chr(39) * 2)}', '{digest}', {len(rows)}) "
        "ON CONFLICT (source_kind, source_sha256) DO NOTHING;"
    )
    sql = f"""BEGIN;
{metadata}
DELETE FROM raw.inventory_snapshot WHERE import_id = '{import_id}';
COPY raw.inventory_snapshot ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv, HEADER true);
{payload}\\.
COMMIT;
"""
    run_psql(database, sql)
    return import_id, len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="supermarket_agent")
    parser.add_argument("--sales", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--snapshot-date", required=True)
    args = parser.parse_args()

    sales_id, sales_count = import_sales(args.database, args.sales)
    inventory_id, inventory_count = import_inventory(
        args.database, args.inventory, args.snapshot_date
    )
    print(
        f"导入完成：销售 {sales_count} 行 ({sales_id})；"
        f"库存/商品 {inventory_count} 行 ({inventory_id})"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
