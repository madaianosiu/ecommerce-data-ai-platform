"""Question embeddings, with the same model as src/rag/embed_reviews.py.

The constants are duplicated here (instead of imported) because embed_reviews.py loads .env and
the ingestion module on import; a test checks that the values stay identical.
"""
import threading
from pathlib import Path
from typing import Protocol

MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
EMBEDDING_DIM = 384
QUERY_PREFIX = "query: "
CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "models"


class Embedder(Protocol):
    model_name: str

    def embed_query(self, text: str) -> list[float]: ...


class E5Embedder:
    model_name = MODEL_NAME

    def __init__(self):
        from sentence_transformers import SentenceTransformer

        # local_files_only: embed_reviews.py has already downloaded the model; the API downloads nothing.
        self._model = SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION,
                                          cache_folder=str(CACHE_DIR), local_files_only=True)
        self._lock = threading.Lock()

    def embed_query(self, text: str) -> list[float]:
        with self._lock:
            vector = self._model.encode([QUERY_PREFIX + text], normalize_embeddings=True,
                                        convert_to_numpy=True, show_progress_bar=False)[0]
        return vector.tolist()
