import logging
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app import queries
from app.config import get_settings
from app.db import connection
from app.embedder import E5Embedder, Embedder
from app.llm import LLMClient, LLMNotConfigured, LLMUnavailable, create_llm
from app.rag import (
    NOT_ENOUGH_EVIDENCE,
    SYSTEM_PROMPT,
    Candidate,
    build_notes,
    build_user_prompt,
    candidate_limit,
    dedupe_candidates,
)
from app.routers._responses import AUTH_ERRORS, DB_ERRORS
from app.schemas import AskRequest, AskResponse, AskSource, ErrorResponse

log = logging.getLogger("app.ask")

router = APIRouter(tags=["ask"], responses={
    **AUTH_ERRORS, **DB_ERRORS,
    502: {"model": ErrorResponse, "description": "Modelul de limbaj a refuzat sau a eșuat cererea."},
})

_embedder_lock = threading.Lock()

LLM_RETRY_HINT = "The language model is temporarily unavailable. Please retry in about a minute."


# ---------------------------------------------------------------- dependențe (înlocuite în teste)
def get_embedder(request: Request) -> Embedder:
    """Modelul e încărcat o dată la pornire (lifespan); dacă preload e oprit, la primul apel."""
    embedder = getattr(request.app.state, "embedder", None)
    if embedder is not None:
        return embedder
    with _embedder_lock:
        embedder = getattr(request.app.state, "embedder", None)
        if embedder is None:
            try:
                embedder = E5Embedder()
            except Exception:  # noqa: BLE001
                log.exception("Embedding model could not be loaded")
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Embedding model unavailable") from None
            request.app.state.embedder = embedder
    return embedder


def get_llm(request: Request) -> LLMClient:
    llm = getattr(request.app.state, "llm", None)
    if llm is not None:
        return llm
    try:
        llm = create_llm(get_settings())
    except LLMNotConfigured as exc:
        log.error("LLM not configured: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Language model is not configured") from None
    request.app.state.llm = llm
    return llm


def get_connection_factory(request: Request) -> Callable[[], AbstractContextManager]:
    """Conexiunea se ia din pool doar pentru căutare, nu pe toată durata apelului LLM."""
    return lambda: connection(request)


# ---------------------------------------------------------------- endpoint
@router.post(
    "/ask",
    response_model=AskResponse,
    summary="Întrebare în limbaj natural (RAG peste recenzii)",
    description=(
        "Caută recenziile scrise cele mai apropiate semantic de întrebare (multilingual-e5-small, "
        "pgvector), elimină textele aproape identice și cere modelului de limbaj un răspuns bazat "
        "**doar** pe aceste recenzii, cu citări [n] care corespund listei `sources`. Răspunsul vine "
        "în limba întrebării. Recenziile cu text sunt un subset (≈41%) înclinat spre scoruri mici. "
        "Dacă nu se găsește nicio recenzie, răspunsul spune că nu există suficiente dovezi și LLM-ul "
        "nu e apelat. `route` e deocamdată mereu `rag`."
    ),
)
def ask(
    body: AskRequest,
    embedder: Embedder = Depends(get_embedder),
    llm: LLMClient = Depends(get_llm),
    connection_factory: Callable[[], AbstractContextManager] = Depends(get_connection_factory),
) -> AskResponse:
    score_filter = _score_filter_label(body.min_score, body.max_score)

    try:
        vector = embedder.embed_query(body.question)
    except Exception:  # noqa: BLE001
        log.exception("Embedding failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Embedding model unavailable") from None

    with connection_factory() as conn:  # DatabaseUnavailable -> 503 prin handler-ul global
        rows = queries.search_reviews(conn, vector, embedder.model_name, body.min_score,
                                      body.max_score, candidate_limit(body.top_k))

    candidates = [Candidate(r["review_id"], r["review_score"], r["text"], r["similarity"]) for r in rows]
    sources = dedupe_candidates(candidates, body.top_k)
    # n = aceeași numerotare ca <review id="n"> din prompt (build_user_prompt), deci ca [n] din răspuns.
    source_models = [AskSource(n=i, review_id=s.review_id, review_score=s.review_score, text=s.text,
                               similarity=round(s.similarity, 4)) for i, s in enumerate(sources, start=1)]

    if not sources:
        return AskResponse(answer=NOT_ENOUGH_EVIDENCE, sources=[], retrieved_count=len(rows),
                           notes=build_notes(len(rows), 0, score_filter=score_filter))

    try:
        result = llm.generate(SYSTEM_PROMPT, build_user_prompt(body.question, sources))
    except LLMUnavailable as exc:
        if exc.transient:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, LLM_RETRY_HINT,
                                headers={"Retry-After": "60"}) from None
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The language model request failed.") from None
    if not result.text:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The language model returned an empty answer.")

    usage = (f"prompt={result.prompt_tokens} output={result.output_tokens} "
             f"thinking={result.thinking_tokens} total={result.total_tokens}")
    return AskResponse(
        answer=result.text,
        sources=source_models,
        retrieved_count=len(rows),
        notes=build_notes(len(rows), len(sources), model=result.model, usage=usage,
                          used_fallback=result.used_fallback, score_filter=score_filter),
    )


def _score_filter_label(min_score: int | None, max_score: int | None) -> str | None:
    if min_score is None and max_score is None:
        return None
    return f"{min_score or 1}-{max_score or 5} stars"
