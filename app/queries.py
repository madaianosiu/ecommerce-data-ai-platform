"""SQL către schema core. Toate valorile de la utilizator sunt parametri (%(name)s);
singurele părți dinamice din text sunt identificatori aleși dintr-o listă fixă (sql.Identifier)."""
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
    """Serie + total (rândul cu period NULL din grouping sets). Venit = price, is_revenue = true."""
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
    # Statul clientului vine din dim_customers (adresa din comanda cea mai recentă).
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
    """Keyset: ordine stabilă (order_purchase_at desc, order_id desc); întoarce limit + 1 rânduri
    ca apelantul să știe dacă există o pagină următoare."""
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
