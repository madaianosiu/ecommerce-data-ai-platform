-- Grain: review_id + order_id. În sursă, același review_id apare pe mai multe comenzi
-- (789 de id-uri), mereu cu conținut identic și fără duplicate exacte, deci nu deduplicăm:
-- fiecare rând leagă recenzia de o comandă distinctă.
select
    review_id,
    order_id,
    review_score,
    review_comment_title,
    review_comment_message,
    review_comment_message is not null      as has_comment,
    review_created_at,
    review_created_at::date                 as review_created_date,
    review_answered_at
from {{ ref('stg_order_reviews') }}
