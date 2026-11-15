"""Main entry point for the FastAPI application."""

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from app.api.health import router as health_router
from app.api.messages import router as messages_router
from app.api.metrics import router as metrics_router
from app.api.products import router as products_router
from app.api.promo import router as promo_router
from app.api.reports import router as reports_router
from app.api.transactions import router as transactions_router
from app.infrastructure.monitoring.metrics import record_http_request


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    application = FastAPI(
        title="Asisten Toko Pancing Papa API",
        description="API for tracking transactions, inventory, and reports for tackle shop.",
        version="0.1.0",
    )

    @application.middleware("http")
    async def monitor_http_requests(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Record request count and latency metrics for API endpoints."""
        started_at = time.monotonic()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            if route_path != "/metrics":
                record_http_request(
                    method=request.method,
                    path=route_path,
                    status_code=status_code,
                    duration_seconds=time.monotonic() - started_at,
                )

    application.include_router(health_router)
    application.include_router(messages_router)
    application.include_router(metrics_router)
    application.include_router(products_router)
    application.include_router(promo_router)
    application.include_router(reports_router)
    application.include_router(transactions_router)
    return application


app = create_app()
