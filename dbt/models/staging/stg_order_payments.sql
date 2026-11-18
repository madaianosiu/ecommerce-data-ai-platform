select
    trim(order_id)                      as order_id,
    payment_sequential::integer         as payment_sequential,
    trim(payment_type)                  as payment_type,
    payment_installments::integer       as payment_installments,
    payment_value::numeric(12, 2)       as payment_value_brl,
    _loaded_at
from {{ source('raw', 'order_payments') }}
