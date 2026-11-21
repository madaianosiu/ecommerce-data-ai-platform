-- Reconciliation: orders that have payments but no row in fact_order_items.
-- Explains the difference between total payments and items + freight; the facts are untouched.
select
    o.order_id,
    o.order_status,
    o.order_purchase_date,
    count(*)                                            as payments_count,
    string_agg(distinct p.payment_type, ', ')           as payment_types,
    sum(p.payment_value_brl)                            as payment_value_brl,
    sum(p.payment_value_eur)                            as payment_value_eur
from {{ ref('fact_payments') }} as p
join {{ ref('fact_orders') }} as o using (order_id)
where not exists (
    select 1
    from {{ ref('fact_order_items') }} as i
    where i.order_id = p.order_id
)
group by o.order_id, o.order_status, o.order_purchase_date
