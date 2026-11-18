select
    trim(order_id)                               as order_id,
    trim(customer_id)                            as customer_id,
    trim(order_status)                           as order_status,
    order_purchase_timestamp::timestamp          as order_purchase_at,
    order_approved_at::timestamp                 as order_approved_at,
    order_delivered_carrier_date::timestamp      as order_delivered_carrier_at,
    order_delivered_customer_date::timestamp     as order_delivered_customer_at,
    order_estimated_delivery_date::timestamp     as order_estimated_delivery_at,
    _loaded_at
from {{ source('raw', 'orders') }}
