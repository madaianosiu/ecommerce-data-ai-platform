select
    trim(order_id)                      as order_id,
    order_item_id::integer              as order_item_id,
    trim(product_id)                    as product_id,
    trim(seller_id)                     as seller_id,
    shipping_limit_date::timestamp      as shipping_limit_at,
    price::numeric(12, 2)               as price_brl,
    freight_value::numeric(12, 2)       as freight_value_brl,
    _loaded_at
from {{ source('raw', 'order_items') }}
