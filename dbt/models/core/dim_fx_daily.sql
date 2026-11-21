-- One row per calendar day in dim_date, with the latest BRL->EUR rate published on or before that day
-- (as-of). The ECB does not publish on weekends and holidays: then is_filled = true.
with rates as (
    select rate_date, rate
    from {{ ref('stg_fx_rates') }}
    where base_currency = 'BRL' and quote_currency = 'EUR'
)

select
    d.date_day                      as date,
    fx.rate                         as rate_brl_eur,
    fx.rate_date                    as rate_source_date,
    fx.rate_date <> d.date_day      as is_filled
from {{ ref('dim_date') }} as d
left join lateral (
    select r.rate_date, r.rate
    from rates as r
    where r.rate_date <= d.date_day
    order by r.rate_date desc
    limit 1
) as fx on true
