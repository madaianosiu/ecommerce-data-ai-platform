"""check_llm.py with the SDK mocked: no network, no real key."""
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors

from rag.check_llm import classify_error, redact, run_check, suggest_flash

FAKE_KEY = "fake-test-key-not-real-1234567890abcdef"
ENV = {"LLM_PROVIDER": "gemini", "LLM_MODEL": "gemini-3.8-flash", "LLM_API_KEY": FAKE_KEY}


def api_error(code: int, status: str, message: str) -> errors.APIError:
    payload = {"error": {"code": code, "status": status, "message": message}}
    cls = errors.ClientError if code < 500 else errors.ServerError
    return cls(code, payload)


class FakeModels:
    def __init__(self, exc=None, available=()):
        self.exc, self.available, self.calls = exc, available, 0

    def generate_content(self, model, contents, config=None):
        self.calls += 1
        self.config = config
        if self.exc:
            raise self.exc
        usage = SimpleNamespace(prompt_token_count=8, candidates_token_count=1,
                                thoughts_token_count=12, total_token_count=21)
        return SimpleNamespace(text="OK\n", usage_metadata=usage, model_version=model)

    def list(self):
        return [SimpleNamespace(name=f"models/{n}", supported_actions=["generateContent"])
                for n in self.available]


def run(exc=None, available=(), env=ENV):
    models = FakeModels(exc, available)
    lines: list[str] = []
    code = run_check(env, client_factory=lambda key: SimpleNamespace(models=models), out=lines.append)
    return code, "\n".join(lines), models


# ---------------------------------------------------------------- success
def test_success_reports_model_and_tokens_with_one_request():
    code, out, models = run()
    assert code == 0 and models.calls == 1
    assert "SUCCESS" in out and "gemini-3.8-flash" in out
    assert "prompt=8 output=1 thinking=12 total=21" in out
    assert FAKE_KEY not in out
    assert models.config.thinking_config.thinking_level.value == "LOW"


# ---------------------------------------------------------------- error mapping
@pytest.mark.parametrize("exc, category", [
    (api_error(401, "UNAUTHENTICATED", "Request had invalid authentication credentials."), "auth"),
    (api_error(403, "PERMISSION_DENIED", "Method doesn't allow unregistered callers."), "auth"),
    (api_error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."), "auth"),
    (api_error(404, "NOT_FOUND", "models/gemini-3.8-flash is not found for API version v1beta"), "model_not_found"),
    (api_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded for metric generate_content_free_tier_requests"), "rate_limit"),
    (api_error(503, "UNAVAILABLE", "The model is overloaded."), "server"),
    (api_error(504, "DEADLINE_EXCEEDED", "Deadline expired before operation could complete."), "timeout"),
    (api_error(400, "INVALID_ARGUMENT", "Invalid value at contents"), "api"),
    (httpx.ConnectError("getaddrinfo failed"), "network"),
    (httpx.ReadTimeout("timed out"), "network"),
    (TimeoutError(), "network"),
    (RuntimeError("boom"), "unexpected"),
])
def test_errors_are_mapped_to_plain_categories(exc, category):
    assert classify_error(exc)[0] == category
    code, out, _ = run(exc)
    assert code == 1
    assert f"FAILURE ({category})" in out


def test_404_lists_models_and_suggests_closest_flash():
    available = ["gemini-3.5-pro", "gemini-3.5-flash", "gemini-3.5-flash-lite", "embedding-001"]
    code, out, _ = run(api_error(404, "NOT_FOUND", "not found"), available=available)
    assert code == 1
    assert "  gemini-3.5-pro" in out and "embedding-001" not in out
    assert "Closest Flash model: gemini-3.5-flash" in out


def test_suggest_flash():
    assert suggest_flash("gemini-3.8-flash", ["gemini-3.5-flash-lite", "gemini-3.5-flash"]) == "gemini-3.5-flash"
    assert suggest_flash("gemini-3.8-flash", ["gemini-3.5-pro"]) is None


# ---------------------------------------------------------------- the key never appears
@pytest.mark.parametrize("exc", [
    api_error(400, "INVALID_ARGUMENT", f"API key not valid: {FAKE_KEY}"),
    api_error(403, "PERMISSION_DENIED", f"denied for https://x/v1beta/models?key={FAKE_KEY}"),
    httpx.ConnectError(f"failed https://generativelanguage.googleapis.com/?key={FAKE_KEY}"),
    RuntimeError(f"header x-goog-api-key: {FAKE_KEY}"),
])
def test_key_never_appears_in_output_even_if_the_error_contains_it(exc):
    _, out, _ = run(exc)
    assert FAKE_KEY not in out
    for i in range(0, len(FAKE_KEY) - 8):  # not even fragments of 8+ characters
        assert FAKE_KEY[i:i + 8] not in out


def test_redact():
    assert redact(f"a {FAKE_KEY} b", FAKE_KEY) == "a [REDACTED] b"
    assert redact("https://h/x?key=abc123&alt=json", None) == "https://h/x?key=[REDACTED]&alt=json"


# ---------------------------------------------------------------- configuration
@pytest.mark.parametrize("missing", ["LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY"])
def test_missing_or_empty_variable_stops_before_any_request(missing):
    code, out, models = run(env={**ENV, missing: "  "})
    assert code == 2 and models.calls == 0
    assert f"{missing} is missing or empty" in out


def test_wrong_provider_is_rejected():
    code, out, models = run(env={**ENV, "LLM_PROVIDER": "openai"})
    assert code == 2 and models.calls == 0
    assert FAKE_KEY not in out
