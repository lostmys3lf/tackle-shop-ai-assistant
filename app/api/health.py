"""Healthcheck endpoint router."""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from app.services.health_service import check_system_health

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    """Schema representing service health check response."""

    status: str
    database: str


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={
        status.HTTP_200_OK: {"model": HealthResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse},
    },
    summary="Check service and database health status",
)
def get_health() -> JSONResponse:
    """Return service health and database connection status."""
    health = check_system_health()
    status_code = (
        status.HTTP_200_OK if health.is_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(
        status_code=status_code,
        content={"status": health.status, "database": health.database},
    )
