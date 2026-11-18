select
    trim(seller_id)                     as seller_id,
    trim(seller_zip_code_prefix)        as seller_zip_code_prefix,  -- text: păstrează zerourile din față
    trim(seller_city)                   as seller_city,
    trim(seller_state)                  as seller_state,
    _loaded_at
from {{ source('raw', 'sellers') }}
