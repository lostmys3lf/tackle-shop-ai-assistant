"""Shared FastAPI router helpers."""

from collections.abc import Callable
from typing import TypeVar

from fastapi import HTTPException, status

T = TypeVar("T")


def run_or_bad_request(action: Callable[[], T]) -> T:
    """Execute a service call, translating ValueError into HTTP 400."""
    try:
        return action()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
