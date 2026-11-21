"""API key authentication via the X-API-Key header, compared in constant time."""
import hmac
import logging

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import Settings, get_settings

log = logging.getLogger("app.auth")

api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,  # we handle a missing key ourselves, to return 401 (not 403)
    description="The API key, configured on the server through the API_KEY environment variable.",
)


def require_api_key(
    api_key: str | None = Security(api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.api_key
    if not expected:
        # Fail closed: with no key configured on the server, nobody gets in.
        log.error("API_KEY is not configured; rejecting request")
    if not expected or not api_key or not hmac.compare_digest(
        api_key.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key (header X-API-Key).",
            headers={"WWW-Authenticate": "ApiKey"},
        )
