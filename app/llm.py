"""Interfață LLM pentru /v1/ask: generate(system_prompt, user_prompt) -> LLMResult.

Implementare: Gemini prin google-genai, cu aceeași mapare a erorilor și aceeași redactare a
cheii ca src/rag/check_llm.py. Politică: retry pe 429/503/504 (max 2, backoff exponențial),
timeout de 30 s pe cerere, apoi o singură încercare cu modelul de rezervă. Cheia nu e
niciodată logată: orice mesaj de eroare trece prin redact().
"""
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.config import Settings
from src.rag.check_llm import classify_error, redact

log = logging.getLogger("app.llm")

TIMEOUT_MS = 30_000          # client-side; google-genai îl trimite și serverului (X-Server-Timeout)
MAX_RETRIES = 2              # retry-uri după prima încercare, doar pentru erorile tranzitorii
BACKOFF_BASE_S = 1.0         # 1 s, 2 s (+ jitter mic)
RETRYABLE = {"rate_limit", "server", "timeout"}     # 429, 503 (și alte 5xx), 504
NO_FALLBACK = {"auth"}       # cheia greșită nu se repară schimbând modelul


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    total_tokens: int | None = None
    used_fallback: bool = False
    attempts: int = 1


class LLMUnavailable(Exception):
    """LLM-ul nu a răspuns. `category` vine din classify_error (fără detalii interne)."""

    def __init__(self, category: str, transient: bool):
        super().__init__(category)
        self.category = category
        self.transient = transient


class LLMClient(Protocol):
    def generate(self, system_prompt: str, user_prompt: str, *, temperature: float | None = None,
                 json_output: bool = False) -> LLMResult: ...


@dataclass
class GeminiLLM:
    api_key: str = field(repr=False)
    model: str
    fallback_model: str | None = None
    max_retries: int = MAX_RETRIES
    backoff_base_s: float = BACKOFF_BASE_S
    sleep: Callable[[float], None] = time.sleep
    client: object | None = field(default=None, repr=False)  # injectabil în teste

    def __post_init__(self):
        if self.client is None:
            from google import genai
            from google.genai import types

            self.client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(
                    timeout=TIMEOUT_MS,
                    retry_options=types.HttpRetryOptions(attempts=1),  # retry-urile le facem noi
                ),
            )

    # ------------------------------------------------------------------ API public
    def generate(self, system_prompt: str, user_prompt: str, *, temperature: float | None = None,
                 json_output: bool = False) -> LLMResult:
        """temperature=None păstrează valoarea implicită a modelului (folosit de RAG);
        json_output cere un răspuns application/json (clasificarea întrebărilor)."""
        options = {"temperature": temperature, "json_output": json_output}
        try:
            return self._generate_with_retries(self.model, system_prompt, user_prompt,
                                               retries=self.max_retries, used_fallback=False, **options)
        except LLMUnavailable as main_error:
            if not self.fallback_model or self.fallback_model == self.model \
                    or main_error.category in NO_FALLBACK:
                raise
            log.warning("LLM main model %s failed (%s); trying fallback %s once",
                        self.model, main_error.category, self.fallback_model)
            return self._generate_with_retries(self.fallback_model, system_prompt, user_prompt,
                                               retries=0, used_fallback=True, **options)

    # ------------------------------------------------------------------ intern
    def _generate_with_retries(self, model: str, system_prompt: str, user_prompt: str,
                               retries: int, used_fallback: bool, temperature: float | None = None,
                               json_output: bool = False) -> LLMResult:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._call(model, system_prompt, user_prompt, temperature, json_output)
                return self._to_result(response, model, used_fallback, attempt)
            except Exception as exc:  # noqa: BLE001 - mapăm orice eroare la o categorie
                category, message = classify_error(exc)
                log.warning("LLM %s attempt %d failed: %s", model, attempt, redact(message, self.api_key))
                if category in RETRYABLE and attempt <= retries:
                    delay = self.backoff_base_s * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
                    self.sleep(delay)
                    continue
                raise LLMUnavailable(category, transient=category in RETRYABLE | {"network"}) from None

    def _call(self, model: str, system_prompt: str, user_prompt: str,
              temperature: float | None = None, json_output: bool = False):
        from google.genai import types

        extra = {}
        if temperature is not None:
            extra["temperature"] = temperature
        if json_output:
            extra["response_mime_type"] = "application/json"
        return self.client.models.generate_content(
            model=model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                # LOW: cel mai mic nivel acceptat de modelele 3.x Flash testate (MINIMAL e refuzat).
                thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
                **extra,
            ),
        )

    @staticmethod
    def _to_result(response, model: str, used_fallback: bool, attempts: int) -> LLMResult:
        usage = getattr(response, "usage_metadata", None)
        return LLMResult(
            text=(response.text or "").strip(),
            model=getattr(response, "model_version", None) or model,
            prompt_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            thinking_tokens=getattr(usage, "thoughts_token_count", None) or 0,
            total_tokens=getattr(usage, "total_token_count", None),
            used_fallback=used_fallback,
            attempts=attempts,
        )


class LLMNotConfigured(Exception):
    pass


def create_llm(settings: Settings) -> LLMClient:
    """Alege implementarea după LLM_PROVIDER (deocamdată doar 'gemini')."""
    if settings.llm_provider != "gemini":
        raise LLMNotConfigured(f"unsupported LLM_PROVIDER '{settings.llm_provider}' (only 'gemini')")
    if not settings.llm_api_key or not settings.llm_model:
        raise LLMNotConfigured("LLM_API_KEY and LLM_MODEL must be set")
    return GeminiLLM(api_key=settings.llm_api_key, model=settings.llm_model,
                     fallback_model=settings.llm_fallback_model or None)
