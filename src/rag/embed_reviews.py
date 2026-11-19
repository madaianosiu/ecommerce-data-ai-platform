"""Embedding-uri pentru textul recenziilor (core.fact_reviews) în core.review_embeddings.

Model local multilingv (sentence-transformers), un embedding per review_id cu text.
Repetabil: se calculează doar recenziile care nu au încă embedding pentru acest model
(cheia review_id + model_name); fiecare batch se comite separat, deci o rulare
întreruptă continuă de unde a rămas. Fiecare rulare se loghează în raw.etl_log.

Tabelul e definit în sql/init/05_pgvector.sql.
Rulare: python src/rag/embed_reviews.py [--batch-size 64] [--limit N]
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

# Aceeași conexiune (.env + POSTGRES_*) și același format de log ca scripturile de ingestie.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingest"))
from load_csv import LOG_DDL, connect, write_log  # noqa: E402

MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"  # commit fixat pe Hugging Face
EMBEDDING_DIM = 384
# Modelele e5 au fost antrenate cu prefixe: "passage: " pentru documente, "query: " pentru căutări.
PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "
CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "models"  # ignorat de git

TABLE = "review_embeddings"
SOURCE = "core.fact_reviews"
DEFAULT_BATCH_SIZE = 64

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Review:
    review_id: str
    text: str
    score: int


# ---------------------------------------------------------------- funcții pure (testate)
def clean_text(text: str | None) -> str | None:
    """Spațiile multiple / \\r\\n devin un singur spațiu; textul gol devine None."""
    if text is None:
        return None
    cleaned = _WHITESPACE.sub(" ", text).strip()
    return cleaned or None


def dedupe_reviews(rows: Iterable[tuple[str, str | None, int]]) -> list[Review]:
    """Un Review per review_id (primul întâlnit), doar cele cu text nevid, sortate după id.
    În sursă, același review_id apare pe mai multe comenzi cu același text și scor."""
    seen: dict[str, Review] = {}
    for review_id, text, score in rows:
        cleaned = clean_text(text)
        if cleaned is None or review_id in seen:
            continue
        seen[review_id] = Review(review_id, cleaned, int(score))
    return [seen[k] for k in sorted(seen)]


def select_new(reviews: Iterable[Review], existing_ids: set[str]) -> list[Review]:
    """Recenziile care nu au încă embedding pentru modelul curent."""
    return [r for r in reviews if r.review_id not in existing_ids]


def batched(items: list, size: int) -> Iterator[list]:
    if size < 1:
        raise ValueError("batch size must be >= 1")
    for start in range(0, len(items), size):
        yield items[start:start + size]


# ---------------------------------------------------------------- model
def load_model():
    # Import întârziat: testele funcțiilor pure nu au nevoie de torch / sentence-transformers.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION, cache_folder=str(CACHE_DIR))


def embed(model, texts: list[str], prefix: str):
    """Vectori normalizați (lungime 1), deci distanța cosinus e coerentă între rulări."""
    return model.encode([prefix + t for t in texts], normalize_embeddings=True,
                        convert_to_numpy=True, show_progress_bar=False)


# ---------------------------------------------------------------- baza de date
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
    """Întoarce (expected, loaded) pentru etl_log."""
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
        except Exception as exc:  # fiecare conn.transaction() a făcut deja rollback
            status, error = "failed", str(exc)

        write_log(conn, run_id, TABLE, f"{SOURCE} ({MODEL_NAME})", expected, loaded, status, error, started)

    print(f"{TABLE:22} {status:8} expected={expected} loaded={loaded} "
          f"duration={time.perf_counter() - t0:.1f}s" + (f"  ERROR: {error}" if error else ""))
    if status != "success":
        sys.exit(1)


if __name__ == "__main__":
    main()
