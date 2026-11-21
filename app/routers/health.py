from fastapi import APIRouter, Request, Response, status

from app import queries
from app.db import DatabaseUnavailable, connection
from app.routers._responses import DB_ERRORS
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service status",
    description="No authentication. Also checks the database connection (select 1). "
                "Returns 503 if the database does not respond.",
    responses={503: {"model": HealthResponse, "description": DB_ERRORS[503]["description"],
                     "content": {"application/json": {"example": {"status": "degraded",
                                                                  "database": "unavailable"}}}}},
)
def health(request: Request, response: Response) -> HealthResponse:
    try:
        with connection(request) as conn:
            queries.ping(conn)
    except DatabaseUnavailable:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="degraded", database="unavailable")
    return HealthResponse(status="ok", database="ok")
