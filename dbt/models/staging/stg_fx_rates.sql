select
    rate_date,
    trim(base)::text        as base_currency,
    trim(quote)::text       as quote_currency,
    rate,
    _loaded_at
from {{ source('raw', 'fx_rates') }}
