-- Grain: one order. No amounts: revenue lives in fact_order_items, payments in fact_payments.
select
    o.order_id,
    o.customer_id,                                  -- per-order id (from the source)
    c.customer_unique_id,                           -- the real customer -> dim_customers
    o.order_status,
    o.order_purchase_at,
    o.order_purchase_at::date                       as order_purchase_date,
    o.order_approved_at,
    o.order_delivered_carrier_at,
    o.order_delivered_customer_at,
    o.order_estimated_delivery_at,
    o.order_delivered_customer_at::date
        - o.order_purchase_at::date                 as delivery_days,
    case
        when o.order_delivered_customer_at is null then null
        else o.order_delivered_customer_at::date > o.order_estimated_delivery_at::date
    end                                             as is_late,
    o.order_status = 'delivered'
        and o.order_delivered_customer_at is null   as has_delivery_date_issue
from {{ ref('stg_orders') }} as o
left join {{ ref('stg_customers') }} as c using (customer_id)
