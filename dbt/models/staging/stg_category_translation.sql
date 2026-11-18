select
    trim(product_category_name)             as product_category_name,
    trim(product_category_name_english)     as product_category_name_english,
    _loaded_at
from {{ source('raw', 'category_translation') }}
