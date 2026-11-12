"""Tests for the GET /health endpoint."""

from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_check_success():
    """Verify /health returns 200 when database connectivity is healthy."""
    with patch(
        "app.services.health_service.check_database_connection",
        return_value=True,
    ):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "ok"}


def test_health_check_database_failure():
    """Verify /health returns 503 when database connectivity fails."""
    with patch(
        "app.services.health_service.check_database_connection",
        return_value=False,
    ):
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json() == {"status": "error", "database": "error"}
