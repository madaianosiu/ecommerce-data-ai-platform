"""Embedding pentru întrebări, cu același model ca src/rag/embed_reviews.py.

Constantele sunt dublate aici (în loc de import) pentru că embed_reviews.py încarcă .env și
modulul de ingestie la import; un test verifică faptul că valorile rămân identice.
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

        # local_files_only: modelul e deja descărcat de embed_reviews.py; API-ul nu descarcă nimic.
        self._model = SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION,
                                          cache_folder=str(CACHE_DIR), local_files_only=True)
        self._lock = threading.Lock()

    def embed_query(self, text: str) -> list[float]:
        with self._lock:
            vector = self._model.encode([QUERY_PREFIX + text], normalize_embeddings=True,
                                        convert_to_numpy=True, show_progress_bar=False)[0]
        return vector.tolist()
