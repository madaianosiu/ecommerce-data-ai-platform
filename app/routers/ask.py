import logging
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app import queries, text_to_sql
from app.config import get_settings
from app.db import connection
from app.embedder import E5Embedder, Embedder
from app.llm import LLMClient, LLMNotConfigured, LLMResult, LLMUnavailable, create_llm
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
from app.routing import DEFAULT_UNSUPPORTED_REASON, RouteDecision, classify
from app.schemas import (
    AskRequest,
    AskResponse,
    AskResult,
    AskSource,
    ErrorResponse,
    SqlAskResponse,
    UnsupportedAskResponse,
)
from app.sql_guard import ROW_CAP
from app.text_to_sql import SqlContext, SqlRouteError, UNSUPPORTED_ANSWER

log = logging.getLogger("app.ask")

router = APIRouter(tags=["ask"], responses={
    **AUTH_ERRORS, **DB_ERRORS,
    422: {"model": ErrorResponse, "description": "Invalid request, or a question that could not be turned "
                                                 "into a safe SQL query."},
    502: {"model": ErrorResponse, "description": "The language model refused or failed the request."},
})

_embedder_lock = threading.Lock()
_sql_context_lock = threading.Lock()

LLM_RETRY_HINT = "The language model is temporarily unavailable. Please retry in about a minute."


# ---------------------------------------------------------------- dependencies (overridden in tests)
def get_embedder(request: Request) -> Embedder:
    """The model is loaded once at startup (lifespan); if preload is off, on the first call."""
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


def get_embedder_provider(request: Request) -> Callable[[], Embedder]:
    """Lazy: the sql route does not need the embedding model."""
    return lambda: get_embedder(request)


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
    """A pool connection is taken only for the search/query, not for the whole LLM call."""
    return lambda: connection(request)


def get_classifier(llm: LLMClient = Depends(get_llm)) -> Callable[[str], RouteDecision]:
    return lambda question: classify(question, llm)


def get_sql_context_provider(request: Request) -> Callable[[], SqlContext]:
    """The core schema description (from dbt + information_schema), built once, on the first sql question."""
    def provider() -> SqlContext:
        ctx = getattr(request.app.state, "sql_context", None)
        if ctx is None:
            with _sql_context_lock:
                ctx = getattr(request.app.state, "sql_context", None)
                if ctx is None:
                    with connection(request) as conn:
                        ctx = text_to_sql.load_sql_context(conn)
                    request.app.state.sql_context = ctx
        return ctx
    return provider


# ---------------------------------------------------------------- endpoint
@router.post(
    "/ask",
    response_model=AskResult,
    summary="Natural-language question (SQL / RAG routing)",
    description=(
        "A router picks the route for each question (LLM with strict JSON; keywords if the LLM does not answer):\n\n"
        "* **`sql`** (numbers, totals, rankings, rates): the LLM writes a single SELECT over schema `core`, "
        "validated with sqlglot (SELECT only, table allowlist, dangerous functions blocked), run as "
        "`api_reader` in a read-only transaction with a 10 s timeout and at most 200 rows; the answer "
        "cites only numbers from the rows and includes the executed SQL.\n"
        "* **`rag`** (reasons, opinions): semantic search over written reviews and an answer with [n] "
        "citations matching the `sources` list. Reviews with text are a subset (≈41%) that skews toward low scores.\n"
        "* **`unsupported`**: the question is not about this data; the answer says why in one sentence.\n\n"
        "The answer is in the language of the question."
    ),
)
def ask(
    body: AskRequest,
    classifier: Callable[[str], RouteDecision] = Depends(get_classifier),
    llm: LLMClient = Depends(get_llm),
    embedder_provider: Callable[[], Embedder] = Depends(get_embedder_provider),
    connection_factory: Callable[[], AbstractContextManager] = Depends(get_connection_factory),
    sql_context_provider: Callable[[], SqlContext] = Depends(get_sql_context_provider),
):
    decision = classifier(body.question)
    log.info("ask route=%s (by %s)", decision.route, decision.source)

    if decision.route == "unsupported":
        # One sentence with the reason (from the classifier, cleaned by clean_reason; otherwise the default).
        reason = decision.reason or DEFAULT_UNSUPPORTED_REASON
        return UnsupportedAskResponse(answer=f"{UNSUPPORTED_ANSWER} {reason}", notes=[_route_note(decision)])
    if decision.route == "sql":
        return _answer_sql(body, decision, llm, connection_factory, sql_context_provider)
    return _answer_rag(body, embedder_provider(), llm, connection_factory)


# ---------------------------------------------------------------- sql route
def _answer_sql(body: AskRequest, decision: RouteDecision, llm: LLMClient,
                connection_factory, sql_context_provider) -> SqlAskResponse:
    ctx = sql_context_provider()   # DatabaseUnavailable -> 503 via the global handler
    try:
        outcome = text_to_sql.answer_with_sql(body.question, llm, ctx, connection_factory,
                                              expected_user=get_settings().db_user)
    except LLMUnavailable as exc:
        raise _llm_http_error(exc) from None
    except SqlRouteError as exc:
        log.warning("SQL route failed: %s", exc.internal)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.public_message) from None

    notes = [_route_note(decision)]
    if outcome.cannot_answer:
        notes.append("No query was run: the model reported that the schema cannot answer this question.")
    else:
        notes.append(f"Read-only query on schema core as api_reader, {text_to_sql.STATEMENT_TIMEOUT_MS // 1000} s "
                     f"timeout, capped at {ROW_CAP} rows.")
        if outcome.truncated:
            notes.append(f"The result reached the {ROW_CAP}-row cap and may be incomplete.")
        if outcome.attempts > 1:
            notes.append("The first generated query was rejected or failed; this is the repaired query.")
    if body.min_score is not None or body.max_score is not None:
        notes.append("min_score/max_score only filter review search (route rag) and were ignored.")
    notes.append(_usage_note(outcome.llm_results))
    return SqlAskResponse(answer=outcome.answer, sql=outcome.sql, rows=outcome.rows,
                          row_count=len(outcome.rows), notes=notes)


def _route_note(decision: RouteDecision) -> str:
    by = "the language model" if decision.source == "llm" else "keyword rules (language model unavailable)"
    return f"Route: {decision.route} (chosen by {by})."


def _usage_note(results: list[LLMResult]) -> str:
    total = sum(r.total_tokens or 0 for r in results)
    models = sorted({r.model + (" (fallback)" if r.used_fallback else "") for r in results})
    return f"Model: {', '.join(models)}; {len(results)} LLM call(s) for this route, total tokens={total}"


def _llm_http_error(exc: LLMUnavailable) -> HTTPException:
    if exc.transient:
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, LLM_RETRY_HINT, headers={"Retry-After": "60"})
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "The language model request failed.")


# ---------------------------------------------------------------- rag route (unchanged)
def _answer_rag(body: AskRequest, embedder: Embedder, llm: LLMClient, connection_factory) -> AskResponse:
    score_filter = _score_filter_label(body.min_score, body.max_score)

    try:
        vector = embedder.embed_query(body.question)
    except Exception:  # noqa: BLE001
        log.exception("Embedding failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Embedding model unavailable") from None

    with connection_factory() as conn:  # DatabaseUnavailable -> 503 via the global handler
        rows = queries.search_reviews(conn, vector, embedder.model_name, body.min_score,
                                      body.max_score, candidate_limit(body.top_k))

    candidates = [Candidate(r["review_id"], r["review_score"], r["text"], r["similarity"]) for r in rows]
    sources = dedupe_candidates(candidates, body.top_k)
    # n = the same numbering as <review id="n"> in the prompt (build_user_prompt), so as [n] in the answer.
    source_models = [AskSource(n=i, review_id=s.review_id, review_score=s.review_score, text=s.text,
                               similarity=round(s.similarity, 4)) for i, s in enumerate(sources, start=1)]

    if not sources:
        return AskResponse(answer=NOT_ENOUGH_EVIDENCE, sources=[], retrieved_count=len(rows),
                           notes=build_notes(len(rows), 0, score_filter=score_filter))

    try:
        result = llm.generate(SYSTEM_PROMPT, build_user_prompt(body.question, sources))
    except LLMUnavailable as exc:
        raise _llm_http_error(exc) from None
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
