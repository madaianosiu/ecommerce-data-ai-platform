import csv
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

load_dotenv()

DATA_DIR = Path("data")

FILE_TO_TABLE = {
    "olist_customers_dataset.csv": "customers",
    "olist_geolocation_dataset.csv": "geolocation",
    "olist_order_items_dataset.csv": "order_items",
    "olist_order_payments_dataset.csv": "order_payments",
    "olist_order_reviews_dataset.csv": "order_reviews",
    "olist_orders_dataset.csv": "orders",
    "olist_products_dataset.csv": "products",
    "olist_sellers_dataset.csv": "sellers",
    "product_category_name_translation.csv": "category_translation",
}

LOG_DDL = """
CREATE TABLE IF NOT EXISTS raw.etl_log (
    id bigserial PRIMARY KEY,
    run_id uuid,
    table_name text,
    source_file text,
    rows_expected integer,
    rows_loaded integer,
    status text,
    error text,
    started_at timestamptz,
    finished_at timestamptz
)
"""


def connect():
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def read_header(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [name.strip() for name in next(csv.reader(f))]


def count_records(path):
    with open(path, newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.reader(f)) - 1  # without the header


def write_log(conn, run_id, table, filename, expected, loaded, status, error, started):
    with conn.transaction():
        conn.execute(
            """INSERT INTO raw.etl_log
               (run_id, table_name, source_file, rows_expected, rows_loaded,
                status, error, started_at, finished_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())""",
            (str(run_id), table, filename, expected, loaded, status, error, started),
        )


def load_table(conn, run_id, filename, table):
    path = DATA_DIR / filename
    started = datetime.now(timezone.utc)
    expected, loaded = None, 0
    try:
        if not path.exists():
            raise FileNotFoundError(f"Missing file: {path}")
        expected = count_records(path)
        columns = read_header(path)
        target = sql.Identifier("raw", table)
        copy_sql = sql.SQL(
            "COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')"
        ).format(target, sql.SQL(", ").join(map(sql.Identifier, columns)))

        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql.SQL("TRUNCATE {}").format(target))
                with cur.copy(copy_sql) as copy:
                    with open(path, "rb") as f:
                        while chunk := f.read(1 << 16):
                            copy.write(chunk)
                loaded = cur.rowcount
                if loaded != expected:
                    raise ValueError(f"Row count mismatch: expected {expected}, loaded {loaded}")
                cur.execute(
                    sql.SQL("UPDATE {} SET _source_file = %s").format(target), (filename,)
                )
        status, error = "success", None
    except Exception as exc:
        loaded, status, error = 0, "failed", str(exc)

    write_log(conn, run_id, table, filename, expected, loaded, status, error, started)
    print(f"{table:22} {status:8} expected={expected} loaded={loaded}"
          + (f"  ERROR: {error}" if error else ""))
    return status == "success"


def main():
    run_id = uuid.uuid4()
    with connect() as conn:
        with conn.transaction():
            conn.execute(LOG_DDL)
        results = [load_table(conn, run_id, f, t) for f, t in FILE_TO_TABLE.items()]
    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    main()