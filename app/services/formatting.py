"""Shared text, currency, and summary formatting helpers for services."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.domain.models import DomainProduct


def format_rupiah(amount: Decimal) -> str:
    """Format decimal amount into Indonesian Rupiah without decimals (e.g. Rp3.000)."""
    rounded = int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    sign = "-" if rounded < 0 else ""
    formatted = f"{abs(rounded):,}".replace(",", ".")
    return f"{sign}Rp{formatted}"


def serialize_product_summary(product: DomainProduct) -> dict[str, Any]:
    """Serialize core inventory fields of a DomainProduct for API responses."""
    return {
        "product_id": product.id,
        "name": product.name,
        "category": product.category,
        "unit": product.unit,
        "stock_qty": product.stock_qty,
        "min_stock": product.min_stock,
    }
