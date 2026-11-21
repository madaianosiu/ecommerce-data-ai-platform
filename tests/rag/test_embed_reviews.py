"""Tests for src/rag/embed_reviews.py.
The pure functions need neither the model nor the database. The integration test is skipped
automatically if the database is unavailable or embed_reviews.py has not been run yet."""
import os

import psycopg
import pytest

from rag.embed_reviews import (
    EMBEDDING_DIM,
    MODEL_NAME,
    Review,
    batched,
    clean_text,
    dedupe_reviews,
    fetch_existing_ids,
    fetch_reviews,
    select_new,
)


# ---------------------------------------------------------------- batched
def test_batched_splits_with_remainder():
    assert list(batched(list(range(7)), 3)) == [[0, 1, 2], [3, 4, 5], [6]]


def test_batched_exact_multiple_and_empty():
    assert list(batched([1, 2, 3, 4], 2)) == [[1, 2], [3, 4]]
    assert list(batched([], 5)) == []


def test_batched_keeps_order_and_items():
    items = list(range(1000))
    flat = [x for b in batched(items, 64) for x in b]
    assert flat == items
    assert all(len(b) <= 64 for b in batched(items, 64))


@pytest.mark.parametrize("size", [0, -1])
def test_batched_rejects_invalid_size(size):
    with pytest.raises(ValueError):
        list(batched([1], size))


# ---------------------------------------------------------------- clean_text
@pytest.mark.parametrize("raw, expected", [
    ("  Produto ótimo  ", "Produto ótimo"),
    ("Chegou\r\nquebrado\n\n!", "Chegou quebrado !"),
    ("   ", None),
    ("", None),
    (None, None),
])
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


# ---------------------------------------------------------------- dedupe + select_new
def test_dedupe_one_per_review_id_and_skips_empty():
    rows = [
        ("r2", "Bom", 4),
        ("r1", "Atrasou", 1),
        ("r2", "Bom", 4),       # the same review_id on another order
        ("r3", "   ", 5),       # empty text after cleaning
        ("r4", None, 3),
    ]
    assert dedupe_reviews(rows) == [Review("r1", "Atrasou", 1), Review("r2", "Bom", 4)]


def test_select_new_skips_existing():
    reviews = [Review("r1", "a", 1), Review("r2", "b", 2), Review("r3", "c", 3)]
    assert select_new(reviews, {"r2"}) == [Review("r1", "a", 1), Review("r3", "c", 3)]
    assert select_new(reviews, {"r1", "r2", "r3"}) == []
    assert select_new(reviews, set()) == reviews


def test_second_run_selects_nothing():
    reviews = dedupe_reviews([("r1", "x", 5), ("r2", "y", 1), ("r1", "x", 5)])
    first = select_new(reviews, set())
    after_first_run = {r.review_id for r in first}
    assert select_new(reviews, after_first_run) == []


# ---------------------------------------------------------------- integration
@pytest.fixture(scope="module")
def db_conn():
    from rag.embed_reviews import connect  # load_csv.connect: .env + POSTGRES_*

    # load_csv.connect sets no timeout; libpq reads PGCONNECT_TIMEOUT, so the test is skipped
    # within seconds (not minutes) when the database does not respond.
    os.environ.setdefault("PGCONNECT_TIMEOUT", "3")
    try:
        conn = connect()
    except (psycopg.Error, KeyError) as exc:
        pytest.skip(f"integration: database not available ({type(exc).__name__})")
    with conn:
        try:
            with conn.transaction():
                n = conn.execute(
                    "select count(*) from core.review_embeddings where model_name = %s", (MODEL_NAME,)
                ).fetchone()[0]
        except psycopg.Error as exc:
            pytest.skip(f"integration: core.review_embeddings not available ({type(exc).__name__})")
        if n == 0:
            pytest.skip("integration: run src/rag/embed_reviews.py first")
        yield conn


@pytest.mark.integration
def test_every_review_with_text_is_embedded_exactly_once(db_conn):
    reviews = fetch_reviews(db_conn)
    existing = fetch_existing_ids(db_conn, MODEL_NAME)
    assert select_new(reviews, existing) == []          # a new run would have nothing left to do
    assert existing == {r.review_id for r in reviews}   # and there are no extra rows

    # bad_text uses the SQL equivalent of clean_text: first any whitespace -> ' ', then trim
    # (Postgres trim() removes only spaces, not CR/LF, so the order matters).
    with db_conn.transaction():
        dims, min_norm, max_norm, bad_text = db_conn.execute("""
            select min(vector_dims(e.embedding)), min(vector_norm(e.embedding)), max(vector_norm(e.embedding)),
                   count(*) filter (where e.comment_text is distinct from trim(regexp_replace(
                       r.review_comment_message, '\\s+', ' ', 'g')))
            from core.review_embeddings e
            join (select distinct on (review_id) review_id, review_comment_message
                  from core.fact_reviews where review_comment_message is not null) r using (review_id)
            where e.model_name = %s
        """, (MODEL_NAME,)).fetchone()
    assert dims == EMBEDDING_DIM
    assert 0.999 < min_norm <= max_norm < 1.001  # normalized vectors
    assert bad_text == 0
