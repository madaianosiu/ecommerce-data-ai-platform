"""Verifică configurarea cheii LLM (Gemini) cu O SINGURĂ cerere minimă.

Citește LLM_PROVIDER, LLM_MODEL, LLM_API_KEY din .env și afișează: succes/eșec,
modelul folosit și consumul de tokeni. Cheia nu este afișată niciodată (nici parțial):
tot ce se tipărește trece prin redact().

Rulare: python src/rag/check_llm.py
Coduri de ieșire: 0 succes, 1 eroare la apel, 2 configurare lipsă/invalidă.
"""
import difflib
import re
import sys
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

PROMPT = "Reply with the single word OK"
# google-genai: HttpOptions.timeout e în milisecunde și e trimis și serverului (X-Server-Timeout),
# deci Gemini oprește singur cererea la acest termen (HTTP 504 DEADLINE_EXCEEDED). Modelele Flash
# care „gândesc” pot depăși 20 s la prima cerere, de aceea 60 s.
TIMEOUT_MS = 60_000
REQUIRED = ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY")
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

_KEY_PARAM = re.compile(r"(key=)[^&\s\"']+", re.IGNORECASE)


# ---------------------------------------------------------------- siguranță
def redact(text: object, secret: str | None) -> str:
    """Scoate cheia din orice text (și orice parametru ?key=... dintr-un URL)."""
    out = str(text)
    if secret:
        out = out.replace(secret, "[REDACTED]")
    return _KEY_PARAM.sub(r"\1[REDACTED]", out)


# ---------------------------------------------------------------- erori -> mesaje simple
def _network_error_types() -> tuple[type[BaseException], ...]:
    types_: list[type[BaseException]] = [ConnectionError, TimeoutError]
    for mod in ("httpx", "httpx2"):  # SDK-ul poate folosi oricare dintre ele
        try:
            types_.append(__import__(mod).TransportError)
        except (ImportError, AttributeError):
            pass
    return tuple(types_)


def classify_error(exc: BaseException) -> tuple[str, str]:
    """(categorie, mesaj simplu) pentru o excepție de la SDK. Pentru erorile 5xx și 400 generice se
    adaugă mesajul serverului (scurtat); la afișare totul trece prin redact()."""
    from google.genai import errors

    if isinstance(exc, errors.APIError):
        code = exc.code
        hint = f"{exc.status or ''} {exc.message or ''} {exc.details or ''}".upper()
        detail = (exc.message or "").strip().replace("\n", " ")[:200]
        if code in (401, 403) or (code == 400 and ("API_KEY" in hint or "API KEY" in hint)):
            return "auth", (f"HTTP {code}: the API key is invalid, missing or not allowed to use this API. "
                            "Check LLM_API_KEY in .env (create or copy it in Google AI Studio).")
        if code == 404:
            return "model_not_found", f"HTTP 404: model not found or not available for this key."
        if code == 429:
            return "rate_limit", ("HTTP 429: quota or rate limit reached for this model. Wait and retry, "
                                  "or check your limits in Google AI Studio.")
        if code == 504 or (exc.status or "").upper() == "DEADLINE_EXCEEDED":
            return "timeout", (f"HTTP {code}: the request did not finish within {TIMEOUT_MS // 1000} s "
                               "(DEADLINE_EXCEEDED). The key was accepted; retry, or raise TIMEOUT_MS.")
        if code >= 500:
            return "server", f"HTTP {code}: Gemini service error ({exc.status}). Retry later. {detail}".rstrip()
        return "api", f"HTTP {code}: request rejected ({exc.status}). {detail}".rstrip()
    if isinstance(exc, _network_error_types()):
        return "network", f"Network error: could not reach the Gemini API ({type(exc).__name__})."
    return "unexpected", f"Unexpected error: {type(exc).__name__}."


# ---------------------------------------------------------------- listă modele (la 404)
def suggest_flash(model: str, available: list[str]) -> str | None:
    flash = [m for m in available if "flash" in m]
    if not flash:
        return None
    close = difflib.get_close_matches(model, flash, n=1, cutoff=0.0)
    return close[0] if close else flash[0]


def list_gemini_models(client) -> list[str]:
    names = []
    for m in client.models.list():
        name = (m.name or "").removeprefix("models/")
        actions = m.supported_actions or []
        if name.startswith("gemini") and (not actions or "generateContent" in actions):
            names.append(name)
    return sorted(set(names))


# ---------------------------------------------------------------- verificarea propriu-zisă
def read_config(env: dict[str, str | None]) -> tuple[dict[str, str], list[str]]:
    values = {k: (env.get(k) or "").strip() for k in REQUIRED}
    problems = [f"{k} is missing or empty" for k, v in values.items() if not v]
    if values["LLM_PROVIDER"] and values["LLM_PROVIDER"].lower() != "gemini":
        problems.append(f"LLM_PROVIDER must be 'gemini' (got '{values['LLM_PROVIDER']}')")
    return values, problems


def default_client_factory(api_key: str):
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(attempts=1),  # o singură cerere, fără retry-uri
        ),
    )


def _check_config():
    """Gândire redusă (LOW; MINIMAL nu e acceptat de gemini-3.8-flash): verificarea are nevoie de un singur cuvânt, nu de raționament.
    Cu gândirea implicită, cererea a depășit 60 s (504 DEADLINE_EXCEEDED)."""
    from google.genai import types

    return types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
    )


def run_check(env: dict[str, str | None], client_factory: Callable = default_client_factory,
              out: Callable[[str], None] = print) -> int:
    values, problems = read_config(env)
    key = values["LLM_API_KEY"] or None

    def emit(line: str) -> None:
        out(redact(line, key))

    if problems:
        emit("FAILURE: configuration")
        for p in problems:
            emit(f"  - {p}")
        return 2

    model = values["LLM_MODEL"]
    emit(f"provider: gemini   model: {model}")
    try:
        client = client_factory(key)
        response = client.models.generate_content(model=model, contents=PROMPT, config=_check_config())
    except Exception as exc:  # noqa: BLE001 - mapăm orice eroare la un mesaj simplu
        category, message = classify_error(exc)
        emit(f"FAILURE ({category}): {message}")
        if category == "model_not_found":
            _report_models(client_factory, key, model, emit)
        return 1

    usage = response.usage_metadata
    reply = (response.text or "").strip()
    emit(f"SUCCESS: model replied {reply[:40]!r}")
    emit(f"model used: {getattr(response, 'model_version', None) or model}")
    if usage is not None:
        emit(f"tokens: prompt={usage.prompt_token_count} output={usage.candidates_token_count} "
             f"thinking={usage.thoughts_token_count or 0} total={usage.total_token_count}")
    else:
        emit("tokens: usage metadata not returned")
    return 0


def _report_models(client_factory, key, model, emit) -> None:
    try:
        names = list_gemini_models(client_factory(key))
    except Exception as exc:  # noqa: BLE001
        emit(f"Could not list available models: {classify_error(exc)[1]}")
        return
    if not names:
        emit("No Gemini models with generateContent are available for this key.")
        return
    emit("Available Gemini models (generateContent):")
    for n in names:
        emit(f"  {n}")
    suggestion = suggest_flash(model, names)
    if suggestion:
        emit(f"Closest Flash model: {suggestion}  -> set LLM_MODEL={suggestion} in .env")


def main() -> None:
    import os

    load_dotenv(ENV_FILE)
    sys.exit(run_check(dict(os.environ)))


if __name__ == "__main__":
    main()
