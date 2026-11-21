"""Ingest exchange rates from the Frankfurter API into raw.fx_rates_raw / raw.fx_rates.

The tables are defined in sql/init/03_fx_tables.sql.
Run: python src/ingest/ingest_fx.py --start 2016-09-01 --end 2018-10-31
"""
import argparse
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import requests
from psycopg.types.json import Jsonb
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Same connection (.env + POSTGRES_*), same DDL and same log format as load_csv.py.
from load_csv import LOG_DDL, connect, write_log

BASE_URL = "https://api.frankfurter.dev/v1"
TABLE = "fx_rates"
WINDOW_DAYS = 90
TIMEOUT = (5, 30)  # (connect, read) in seconds


def make_session():
    retry = Retry(
        total=5,
        backoff_factor=1.0,  # 0s, 2s, 4s, 8s, 16s between attempts
        # 520-524: Cloudflare errors when the Frankfurter origin does not respond (transient)
        status_forcelist=(429, 500, 502, 503, 504, 520, 521, 522, 523, 524),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def date_windows(start, end, size=WINDOW_DAYS):
    """Split [start, end] into consecutive windows of at most `size` days, without overlap."""
    if size < 1:
        raise ValueError("size must be >= 1")
    cur = start
    while cur <= end:
        win_end = min(cur + timedelta(days=size - 1), end)
        yield cur, win_end
        cur = win_end + timedelta(days=1)


def fetch_window(session, start, end, base, quote):
    url = f"{BASE_URL}/{start.isoformat()}..{end.isoformat()}"
    resp = session.get(url, params={"base": base, "symbols": quote}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.url, resp.json()


def flatten_rates(payload, base, start=None, end=None):
    """{'rates': {'2017-01-02': {'EUR': 0.29}}} -> [(date, base, quote, rate)]

    If the window starts on a day without a rate (weekend/holiday), Frankfurter moves
    start_date back to the previous business day, which belongs to the previous window.
    With start/end, we keep only the days of the requested window.
    """
    rows = []
    for day, quotes in payload.get("rates", {}).items():
        d = date.fromisoformat(day)
        if (start and d < start) or (end and d > end):
            continue
        for quote, rate in quotes.items():
            rows.append((d, base, quote, rate))
    return rows


def save_raw(conn, url, start, end, payload):
    with conn.transaction():
        conn.execute(
            """INSERT INTO raw.fx_rates_raw (request_url, window_start, window_end, payload)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (window_start, window_end, request_url)
               DO UPDATE SET payload = EXCLUDED.payload, fetched_at = now()""",
            (url, start, end, Jsonb(payload)),
        )


def upsert_rates(conn, rows, source):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO raw.fx_rates (rate_date, base, quote, rate, _source_file)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (rate_date, base, quote)
                   DO UPDATE SET rate = EXCLUDED.rate, _loaded_at = now(),
                                 _source_file = EXCLUDED._source_file""",
                [(*row, source) for row in rows],
            )
            return cur.rowcount


def write_etl_log(conn, run_id, source, expected, loaded, status, error, started):
    write_log(conn, run_id, TABLE, source, expected, loaded, status, error, started)


def ingest(conn, session, start, end, base, quote):
    """Return (expected, loaded). The raw JSON is saved before the transformation."""
    expected = loaded = 0
    for w_start, w_end in date_windows(start, end):
        url, payload = fetch_window(session, w_start, w_end, base, quote)
        save_raw(conn, url, w_start, w_end, payload)
        rows = flatten_rates(payload, base, w_start, w_end)
        n = upsert_rates(conn, rows, url)
        expected += len(rows)
        loaded += n
        print(f"  {w_start}..{w_end}: received={len(rows)} upserted={n}")
    if loaded != expected:
        raise ValueError(f"Row count mismatch: expected {expected}, loaded {loaded}")
    return expected, loaded


def main():
    ap = argparse.ArgumentParser(description="Ingest FX rates from Frankfurter API")
    ap.add_argument("--start", type=date.fromisoformat, default=date(2016, 9, 1))
    ap.add_argument("--end", type=date.fromisoformat, default=date(2018, 10, 31))
    ap.add_argument("--base", default="BRL")
    ap.add_argument("--quote", default="EUR")
    args = ap.parse_args()
    if args.start > args.end:
        ap.error("--start must be <= --end")

    run_id = uuid.uuid4()
    source = (f"{BASE_URL}/{args.start}..{args.end}"
              f"?base={args.base}&symbols={args.quote}")
    started = datetime.now(timezone.utc)
    expected, loaded = None, 0

    with connect() as conn:
        with conn.transaction():
            conn.execute(LOG_DDL)
        try:
            expected, loaded = ingest(conn, make_session(), args.start, args.end,
                                      args.base, args.quote)
            status, error = "success", None
        except Exception as exc:  # every conn.transaction() has already rolled back
            status, error = "failed", str(exc)

        write_etl_log(conn, run_id, source, expected, loaded, status, error, started)

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"{TABLE:22} {status:8} expected={expected} loaded={loaded} duration={duration:.1f}s"
          + (f"  ERROR: {error}" if error else ""))
    if status != "success":
        sys.exit(1)


if __name__ == "__main__":
    main()
