-- Un rând per zi calendaristică din dim_date, cu ultimul curs BRL->EUR publicat până în acea zi
-- (as-of). ECB nu publică în weekend și de sărbători: atunci is_filled = true.
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
