-- Nicio linie de comandă nu are voie să rămână fără curs EUR.
select order_id, order_item_id, order_purchase_date
from {{ ref('fact_order_items') }}
where rate_brl_eur is null
   or price_eur is null
   or freight_value_eur is null
