-- Grain: un rând per client real (customer_unique_id).
-- În Olist customer_id se generează per comandă, deci un client cu 3 comenzi are 3 customer_id.
-- Legătura customer_id -> customer_unique_id rămâne în fact_orders (ambele coloane).
-- Adresa: cea din comanda cea mai recentă (252 de clienți au adrese diferite între comenzi).
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
