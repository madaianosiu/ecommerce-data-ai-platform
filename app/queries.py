"""SQL against schema core. Every user-supplied value is a parameter (%(name)s);
the only dynamic parts of the text are identifiers picked from a fixed list (sql.Identifier)."""
from datetime import date, datetime

import psycopg
from psycopg import sql

from app.schemas import Currency, Granularity, TopMetric

_PRICE_COLUMN = {Currency.BRL: "price_brl", Currency.EUR: "price_eur"}
_TOP_ORDER_COLUMN = {TopMetric.revenue: "revenue_brl", TopMetric.quantity: "quantity"}


def ping(conn: psycopg.Connection) -> None:
    conn.execute("select 1").fetchone()


def revenue(conn, date_from: date | None, date_to: date | None,
            granularity: Granularity, currency: Currency) -> list[dict]:
    """Series + total (the row with period NULL from grouping sets). Revenue = price, is_revenue = true."""
    query = sql.SQL("""
        select
            period,
            round(sum(amount), 2)           as revenue,
            count(distinct order_id)        as orders,
            count(*)                        as items
        from (
            select
                date_trunc(%(granularity)s, order_purchase_date::timestamp)::date as period,
                order_id,
                {price} as amount
            from core.fact_order_items
            where is_revenue
              and (%(date_from)s::date is null or order_purchase_date >= %(date_from)s::date)
              and (%(date_to)s::date   is null or order_purchase_date <= %(date_to)s::date)
        ) as items
        group by grouping sets ((period), ())
        order by period nulls first
    """).format(price=sql.Identifier(_PRICE_COLUMN[currency]))
    params = {"granularity": granularity.value, "date_from": date_from, "date_to": date_to}
    return conn.execute(query, params).fetchall()


def delivery_performance(conn, state: str | None) -> dict:
    # The customer's state comes from dim_customers (address from the most recent order).
    return conn.execute("""
        select
            count(*) filter (where o.order_delivered_customer_at is not null)            as orders,
            round(avg(o.delivery_days), 2)::float8                                       as avg_delivery_days,
            count(*) filter (where o.is_late)                                            as late_orders,
            round(100.0 * count(*) filter (where o.is_late)
                  / nullif(count(*) filter (where o.order_delivered_customer_at is not null), 0), 2
            )::float8                                                                    as late_pct,
            count(*) filter (where o.has_delivery_date_issue)                            as excluded_missing_delivery_date
        from core.fact_orders as o
        join core.dim_customers as c using (customer_unique_id)
        where o.order_status = 'delivered'
          and (%(state)s::text is null or c.customer_state = %(state)s::text)
    """, {"state": state}).fetchone()


def top_products(conn, category: str | None, limit: int, metric: TopMetric) -> list[dict]:
    query = sql.SQL("""
        select
            i.product_id,
            p.product_category,
            count(*)                        as quantity,
            round(sum(i.price_brl), 2)      as revenue_brl,
            round(sum(i.price_eur), 2)      as revenue_eur
        from core.fact_order_items as i
        join core.dim_products as p using (product_id)
        where i.is_revenue
          and (%(category)s::text is null or p.product_category = %(category)s::text)
        group by i.product_id, p.product_category
        order by {order_col} desc, i.product_id
        limit %(limit)s
    """).format(order_col=sql.Identifier(_TOP_ORDER_COLUMN[metric]))
    return conn.execute(query, {"category": category, "limit": limit}).fetchall()


def orders_page(conn, status: str | None, after: tuple[datetime, str] | None, limit: int) -> list[dict]:
    """Keyset: stable order (order_purchase_at desc, order_id desc); returns limit + 1 rows
    so the caller knows whether there is a next page."""
    after_ts, after_id = after if after else (None, None)
    return conn.execute("""
        select order_id, customer_unique_id, order_status, order_purchase_at, order_approved_at,
               order_delivered_customer_at, order_estimated_delivery_at, delivery_days, is_late
        from core.fact_orders
        where (%(status)s::text is null or order_status = %(status)s::text)
          and (%(after_ts)s::timestamp is null
               or (order_purchase_at, order_id) < (%(after_ts)s::timestamp, %(after_id)s::text))
        order by order_purchase_at desc, order_id desc
        limit %(limit)s
    """, {"status": status, "after_ts": after_ts, "after_id": after_id, "limit": limit + 1}).fetchall()


def search_reviews(conn, vector: list[float], model_name: str, min_score: int | None,
                   max_score: int | None, limit: int) -> list[dict]:
    """The `limit` nearest candidates by cosine distance (HNSW index).
    The vector is sent as a text parameter and cast in SQL (::vector), with no concatenation."""
    vector_literal = "[" + ",".join(f"{x:.8f}" for x in vector) + "]"
    with conn.transaction():
        # By default HNSW returns at most ef_search (40) rows; with score filters, even fewer.
        # set_config(..., true) is local to the transaction; iterative_scan (pgvector >= 0.8) keeps
        # searching until it finds enough candidates that pass the filters.
        conn.execute("select set_config('hnsw.ef_search', %s, true), "
                     "set_config('hnsw.iterative_scan', 'relaxed_order', true)",
                     (str(max(limit, 40) * 2),))
        rows = conn.execute("""
            select review_id, review_score, comment_text as text,
                   (1 - (embedding <=> %(v)s::vector))::float8 as similarity
            from core.review_embeddings
            where model_name = %(model)s
              and (%(min_score)s::int is null or review_score >= %(min_score)s::int)
              and (%(max_score)s::int is null or review_score <= %(max_score)s::int)
            order by embedding <=> %(v)s::vector
            limit %(limit)s
        """, {"v": vector_literal, "model": model_name, "min_score": min_score,
              "max_score": max_score, "limit": limit}).fetchall()
    # relaxed_order can slightly change the order; restore it exactly.
    return sorted(rows, key=lambda r: (-r["similarity"], r["review_id"]))
