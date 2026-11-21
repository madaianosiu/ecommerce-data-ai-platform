"""Embeddings of the review texts (core.fact_reviews) into core.review_embeddings.

Local multilingual model (sentence-transformers), one embedding per review_id with text.
Repeatable: only reviews that have no embedding yet for this model are computed
(key review_id + model_name); each batch is committed separately, so an interrupted run
resumes where it stopped. Every run is logged in raw.etl_log.

The table is defined in sql/init/05_pgvector.sql.
Run: python src/rag/embed_reviews.py [--batch-size 64] [--limit N]
"""
import argparse
import re
import sys
import time
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pgvector.psycopg import register_vector

# Same connection (.env + POSTGRES_*) and same log format as the ingestion scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingest"))
from load_csv import LOG_DDL, connect, write_log  # noqa: E402

MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"  # pinned Hugging Face commit
EMBEDDING_DIM = 384
# The e5 models were trained with prefixes: "passage: " for documents, "query: " for searches.
PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "
CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "models"  # ignored by git

TABLE = "review_embeddings"
SOURCE = "core.fact_reviews"
DEFAULT_BATCH_SIZE = 64

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Review:
    review_id: str
    text: str
    score: int


# ---------------------------------------------------------------- pure functions (tested)
def clean_text(text: str | None) -> str | None:
    """Multiple spaces / \\r\\n become a single space; empty text becomes None."""
    if text is None:
        return None
    cleaned = _WHITESPACE.sub(" ", text).strip()
    return cleaned or None


def dedupe_reviews(rows: Iterable[tuple[str, str | None, int]]) -> list[Review]:
    """One Review per review_id (the first one seen), only those with non-empty text, sorted by id.
    In the source, the same review_id appears on several orders with the same text and score."""
    seen: dict[str, Review] = {}
    for review_id, text, score in rows:
        cleaned = clean_text(text)
        if cleaned is None or review_id in seen:
            continue
        seen[review_id] = Review(review_id, cleaned, int(score))
    return [seen[k] for k in sorted(seen)]


def select_new(reviews: Iterable[Review], existing_ids: set[str]) -> list[Review]:
    """Reviews that have no embedding yet for the current model."""
    return [r for r in reviews if r.review_id not in existing_ids]


def batched(items: list, size: int) -> Iterator[list]:
    if size < 1:
        raise ValueError("batch size must be >= 1")
    for start in range(0, len(items), size):
        yield items[start:start + size]


# ---------------------------------------------------------------- model
def load_model():
    # Deferred import: the pure-function tests don't need torch / sentence-transformers.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION, cache_folder=str(CACHE_DIR))


def embed(model, texts: list[str], prefix: str):
    """Normalized vectors (length 1), so cosine distance is consistent across runs."""
    return model.encode([prefix + t for t in texts], normalize_embeddings=True,
                        convert_to_numpy=True, show_progress_bar=False)


# ---------------------------------------------------------------- database
def fetch_reviews(conn) -> list[Review]:
    with conn.transaction():
        rows = conn.execute(f"""
            select review_id, review_comment_message, review_score
            from {SOURCE}
            where review_comment_message is not null
            order by review_id, order_id
        """).fetchall()
    return dedupe_reviews(rows)


def fetch_existing_ids(conn, model_name: str) -> set[str]:
    with conn.transaction():
        rows = conn.execute(
            "select review_id from core.review_embeddings where model_name = %s", (model_name,)
        ).fetchall()
    return {r[0] for r in rows}


def insert_batch(conn, batch: list[Review], vectors, model_name: str) -> int:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(
                """insert into core.review_embeddings
                       (review_id, comment_text, review_score, embedding, model_name)
                   values (%s, %s, %s, %s, %s)
                   on conflict (review_id, model_name) do nothing""",
                [(r.review_id, r.text, r.score, v, model_name) for r, v in zip(batch, vectors)],
            )
            return cur.rowcount


def run(conn, batch_size: int, limit: int | None) -> tuple[int, int]:
    """Return (expected, loaded) for etl_log."""
    from tqdm import tqdm

    reviews = fetch_reviews(conn)
    existing = fetch_existing_ids(conn, MODEL_NAME)
    new = select_new(reviews, existing)
    if limit is not None:
        new = new[:limit]
    print(f"reviews with text: {len(reviews)}  already embedded: {len(existing)}  to embed: {len(new)}")
    if not new:
        return 0, 0

    model = load_model()
    loaded = 0
    with tqdm(total=len(new), unit="review", desc="embedding") as bar:
        for batch in batched(new, batch_size):
            vectors = embed(model, [r.text for r in batch], PASSAGE_PREFIX)
            loaded += insert_batch(conn, batch, vectors, MODEL_NAME)
            bar.update(len(batch))
    if loaded != len(new):
        raise ValueError(f"Row count mismatch: expected {len(new)}, loaded {loaded}")
    return len(new), loaded


def main():
    ap = argparse.ArgumentParser(description="Embed review texts into core.review_embeddings")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    ap.add_argument("--limit", type=int, default=None, help="embed at most N new reviews (for trials)")
    args = ap.parse_args()
    if args.batch_size < 1:
        ap.error("--batch-size must be >= 1")

    run_id = uuid.uuid4()
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    expected, loaded = None, 0

    with connect() as conn:
        with conn.transaction():
            conn.execute(LOG_DDL)
            register_vector(conn)
        try:
            expected, loaded = run(conn, args.batch_size, args.limit)
            status, error = "success", None
        except Exception as exc:  # every conn.transaction() has already rolled back
            status, error = "failed", str(exc)

        write_log(conn, run_id, TABLE, f"{SOURCE} ({MODEL_NAME})", expected, loaded, status, error, started)

    print(f"{TABLE:22} {status:8} expected={expected} loaded={loaded} "
          f"duration={time.perf_counter() - t0:.1f}s" + (f"  ERROR: {error}" if error else ""))
    if status != "success":
        sys.exit(1)


if __name__ == "__main__":
    main()
