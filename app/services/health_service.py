"""Service for checking overall system and dependency health."""

from dataclasses import dataclass
from app.infrastructure.db.connection import check_database_connection


@dataclass(frozen=True)
class HealthStatus:
    """System health status representation."""

    status: str
    database: str

    @property
    def is_healthy(self) -> bool:
        """Return True if all components are healthy."""
        return self.status == "ok" and self.database == "ok"


def check_system_health() -> HealthStatus:
    """Check application service and database connectivity health."""
    db_ok = check_database_connection()
    if db_ok:
        return HealthStatus(status="ok", database="ok")
    return HealthStatus(status="error", database="error")
