select
    trim(customer_id)                   as customer_id,
    trim(customer_unique_id)            as customer_unique_id,
    trim(customer_zip_code_prefix)      as customer_zip_code_prefix,  -- text: păstrează zerourile din față
    trim(customer_city)                 as customer_city,
    trim(customer_state)                as customer_state,
    _loaded_at
from {{ source('raw', 'customers') }}
