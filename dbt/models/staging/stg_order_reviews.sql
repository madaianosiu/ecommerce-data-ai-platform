select
    trim(review_id)                                 as review_id,
    trim(order_id)                                  as order_id,
    review_score::smallint                          as review_score,
    nullif(trim(review_comment_title), '')          as review_comment_title,
    nullif(trim(review_comment_message), '')        as review_comment_message,
    review_creation_date::timestamp                 as review_created_at,
    review_answer_timestamp::timestamp              as review_answered_at,
    _loaded_at
from {{ source('raw', 'order_reviews') }}
