-- Grain: order_id + order_item_id. EUR = BRL * cursul as-of din ziua cumpărării comenzii.
-- Venit = sum(price) unde is_revenue (comanda nu e canceled/unavailable). Nu se șterg rânduri.
select
    i.order_id,
    i.order_item_id,
    i.product_id,
    i.seller_id,
    o.order_status,
    o.order_status not in ('canceled', 'unavailable')  as is_revenue,
    o.order_purchase_date,
    i.shipping_limit_at,
    i.price_brl,
    i.freight_value_brl,
    fx.rate_brl_eur,
    i.price_brl * fx.rate_brl_eur           as price_eur,
    i.freight_value_brl * fx.rate_brl_eur   as freight_value_eur
from {{ ref('stg_order_items') }} as i
join {{ ref('fact_orders') }} as o using (order_id)
left join {{ ref('dim_fx_daily') }} as fx on fx.date = o.order_purchase_date
