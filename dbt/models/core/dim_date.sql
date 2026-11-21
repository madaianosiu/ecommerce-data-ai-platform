-- Continuous calendar covering every date found in orders (purchase ... estimated delivery).
with bounds as (
    select
        least(
            min(order_purchase_at), min(order_approved_at), min(order_delivered_carrier_at),
            min(order_delivered_customer_at), min(order_estimated_delivery_at)
        )::date as min_date,
        greatest(
            max(order_purchase_at), max(order_approved_at), max(order_delivered_carrier_at),
            max(order_delivered_customer_at), max(order_estimated_delivery_at)
        )::date as max_date
    from {{ ref('stg_orders') }}
),

days as (
    select generate_series(min_date, max_date, interval '1 day')::date as date_day
    from bounds
)

select
    date_day,
    extract(year from date_day)::integer        as year,
    extract(quarter from date_day)::integer     as quarter,
    extract(month from date_day)::integer       as month,
    to_char(date_day, 'YYYY-MM')                as year_month,
    trim(to_char(date_day, 'Month'))            as month_name,
    extract(day from date_day)::integer         as day_of_month,
    extract(isodow from date_day)::integer      as iso_day_of_week,  -- 1 = Monday
    trim(to_char(date_day, 'Day'))              as day_name,
    extract(week from date_day)::integer        as iso_week,
    extract(isodow from date_day) in (6, 7)     as is_weekend
from days
