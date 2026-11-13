"""Prometheus metrics endpoint router."""

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["Monitoring"])


@router.get(
    "/metrics",
    include_in_schema=False,
    summary="Expose Prometheus metrics for scraping",
)
def get_metrics() -> Response:
    """Return latest Prometheus metrics in text exposition format."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
