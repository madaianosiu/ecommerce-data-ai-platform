"""Căutare semantică în core.review_embeddings (similaritate cosinus, index HNSW).

Rulare: python src/rag/search_reviews.py "atraso na entrega" [-k 5]
"""
import argparse
import sys
from pathlib import Path

from pgvector.psycopg import register_vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from embed_reviews import MODEL_NAME, QUERY_PREFIX, connect, embed, load_model  # noqa: E402


def search(conn, model, query: str, k: int = 5) -> list[tuple]:
    """(review_id, review_score, similarity, comment_text), cele mai apropiate primele."""
    vector = embed(model, [query], QUERY_PREFIX)[0]
    return conn.execute(
        """select review_id, review_score, round((1 - (embedding <=> %(v)s))::numeric, 4) as similarity,
                  comment_text
           from core.review_embeddings
           where model_name = %(model)s
           order by embedding <=> %(v)s
           limit %(k)s""",
        {"v": vector, "model": MODEL_NAME, "k": k},
    ).fetchall()


def main():
    ap = argparse.ArgumentParser(description="Semantic search over review texts")
    ap.add_argument("queries", nargs="+", help="one or more search phrases (any language)")
    ap.add_argument("-k", type=int, default=5)
    args = ap.parse_args()

    model = load_model()
    with connect() as conn:
        register_vector(conn)
        for query in args.queries:
            print(f'\n=== "{query}"')
            for review_id, score, similarity, text in search(conn, model, query, args.k):
                print(f"  {similarity:.4f}  score={score}  {review_id[:8]}  {text}")


if __name__ == "__main__":
    main()
