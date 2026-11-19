"""Autentificare cu API key în header-ul X-API-Key, comparat în timp constant."""
import hmac
import logging

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import Settings, get_settings

log = logging.getLogger("app.auth")

api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,  # tratăm noi lipsa cheii, ca să întoarcem 401 (nu 403)
    description="Cheia API, configurată pe server prin variabila de mediu API_KEY.",
)


def require_api_key(
    api_key: str | None = Security(api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.api_key
    if not expected:
        # Fail closed: fără cheie configurată pe server, nimeni nu trece.
        log.error("API_KEY is not configured; rejecting request")
    if not expected or not api_key or not hmac.compare_digest(
        api_key.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key (header X-API-Key).",
            headers={"WWW-Authenticate": "ApiKey"},
        )
