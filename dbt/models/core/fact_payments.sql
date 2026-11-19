-- Grain: order_id + payment_sequential. Ținut separat de fact_order_items: o comandă are
-- N items și M plăți, iar join-ul lor direct ar multiplica sumele.
select
    p.order_id,
    p.payment_sequential,
    p.payment_type,
    p.payment_installments,
    o.order_purchase_date,
    p.payment_value_brl,
    fx.rate_brl_eur,
    p.payment_value_brl * fx.rate_brl_eur   as payment_value_eur
from {{ ref('stg_order_payments') }} as p
join {{ ref('fact_orders') }} as o using (order_id)
left join {{ ref('dim_fx_daily') }} as fx on fx.date = o.order_purchase_date
