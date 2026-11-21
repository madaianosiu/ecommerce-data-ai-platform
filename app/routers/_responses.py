"""Common error responses, documented in OpenAPI."""
from app.schemas import ErrorResponse

AUTH_ERRORS = {
    401: {"model": ErrorResponse, "description": "The X-API-Key header is missing or wrong."},
}
DB_ERRORS = {
    503: {"model": ErrorResponse, "description": "The database is unavailable."},
}
BAD_REQUEST = {
    400: {"model": ErrorResponse, "description": "Invalid request (e.g. a corrupted cursor)."},
}
