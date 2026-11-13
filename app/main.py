"""Main entry point for the FastAPI application."""

from fastapi import FastAPI
from app.api.health import router as health_router
from app.api.messages import router as messages_router
from app.api.reports import router as reports_router
from app.api.transactions import router as transactions_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    application = FastAPI(
        title="Asisten Toko Pancing Papa API",
        description="API for tracking transactions, inventory, and reports for tackle shop.",
        version="0.1.0",
    )
    application.include_router(health_router)
    application.include_router(messages_router)
    application.include_router(reports_router)
    application.include_router(transactions_router)
    return application


app = create_app()
