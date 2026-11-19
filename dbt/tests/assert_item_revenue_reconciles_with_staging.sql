-- Dovadă că fact_order_items nu dublează și nu pierde venituri: sumele BRL și numărul de
-- rânduri trebuie să fie identice cu stg_order_items. Testul eșuează dacă întoarce rânduri.
with fact as (
    select count(*) as n, sum(price_brl) as price_brl, sum(freight_value_brl) as freight_brl
    from {{ ref('fact_order_items') }}
),

stg as (
    select count(*) as n, sum(price_brl) as price_brl, sum(freight_value_brl) as freight_brl
    from {{ ref('stg_order_items') }}
)

select
    fact.n as fact_rows, stg.n as stg_rows,
    fact.price_brl as fact_price_brl, stg.price_brl as stg_price_brl,
    fact.freight_brl as fact_freight_brl, stg.freight_brl as stg_freight_brl
from fact cross join stg
where fact.n <> stg.n
   or fact.price_brl <> stg.price_brl
   or fact.freight_brl <> stg.freight_brl
