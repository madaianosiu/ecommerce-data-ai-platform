"""Răspunsurile de eroare comune, documentate în OpenAPI."""
from app.schemas import ErrorResponse

AUTH_ERRORS = {
    401: {"model": ErrorResponse, "description": "Header-ul X-API-Key lipsește sau e greșit."},
}
DB_ERRORS = {
    503: {"model": ErrorResponse, "description": "Baza de date nu e disponibilă."},
}
BAD_REQUEST = {
    400: {"model": ErrorResponse, "description": "Cerere invalidă (ex. cursor corupt)."},
}
