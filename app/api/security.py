"""API security dependencies for endpoint authentication."""

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(
    header_value: str | None = Security(api_key_header),
) -> str:
    """Verify that incoming request provides a valid X-API-Key matching configured secret."""
    # Gagal sejak awal kalau API_SECRET kosong agar salah konfigurasi langsung ketahuan
    if not settings.api_secret:
        raise RuntimeError(
            "API_SECRET is empty; set it before calling protected endpoints."
        )
    if not header_value or header_value != settings.api_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kunci API (X-API-Key) tidak valid atau belum disertakan.",
        )
    return header_value
