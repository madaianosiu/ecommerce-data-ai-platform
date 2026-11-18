select
    trim(geolocation_zip_code_prefix)   as zip_code_prefix,
    geolocation_lat::numeric            as latitude,
    geolocation_lng::numeric            as longitude,
    trim(geolocation_city)              as city,
    trim(geolocation_state)             as state,
    _loaded_at
from {{ source('raw', 'geolocation') }}
