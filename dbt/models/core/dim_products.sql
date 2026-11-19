-- Categorie explicită, niciodată NULL:
--   fără categorie în sursă  -> 'unknown'
--   categorie fără traducere -> numele portughez ca fallback
select
    p.product_id,
    coalesce(p.product_category_name, 'unknown')            as product_category_name_pt,
    case
        when p.product_category_name is null then 'unknown'
        else coalesce(t.product_category_name_english, p.product_category_name)
    end                                                     as product_category,
    p.product_category_name is null                         as is_category_missing,
    p.product_category_name is not null
        and t.product_category_name is null                 as is_translation_missing,
    p.product_name_length,
    p.product_description_length,
    p.product_photos_qty,
    p.product_weight_g,
    p.product_length_cm,
    p.product_height_cm,
    p.product_width_cm
from {{ ref('stg_products') }} as p
left join {{ ref('stg_category_translation') }} as t using (product_category_name)
