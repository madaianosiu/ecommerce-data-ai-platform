-- Grain: one row per real customer (customer_unique_id).
-- In Olist, customer_id is generated per order, so a customer with 3 orders has 3 customer_ids.
-- The customer_id -> customer_unique_id link stays in fact_orders (both columns).
-- Address: the one from the most recent order (252 customers have different addresses across orders).
with customer_orders as (
    select
        c.customer_id,
        c.customer_unique_id,
        c.customer_zip_code_prefix,
        c.customer_city,
        c.customer_state,
        o.order_purchase_at
    from {{ ref('stg_customers') }} as c
    left join {{ ref('stg_orders') }} as o using (customer_id)
),

ranked as (
    select
        *,
        row_number() over (
            partition by customer_unique_id
            order by order_purchase_at desc nulls last, customer_id desc
        ) as rn
    from customer_orders
),

agg as (
    select
        customer_unique_id,
        count(*)                    as customer_ids_count,
        min(order_purchase_at)      as first_order_at,
        max(order_purchase_at)      as last_order_at
    from customer_orders
    group by 1
)

select
    r.customer_unique_id,
    r.customer_zip_code_prefix,
    r.customer_city,
    r.customer_state,
    a.customer_ids_count,
    a.first_order_at,
    a.last_order_at
from ranked as r
join agg as a using (customer_unique_id)
where r.rn = 1
