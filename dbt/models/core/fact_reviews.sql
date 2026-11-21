-- Grain: review_id + order_id. In the source, the same review_id appears on several orders
-- (789 ids), always with identical content and no exact duplicates, so we do not deduplicate:
-- each row links the review to a distinct order.
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
