"""Shared text and currency formatting helpers for services."""

from decimal import ROUND_HALF_UP, Decimal


def format_rupiah(amount: Decimal) -> str:
    """Format decimal amount into Indonesian Rupiah without decimals (e.g. Rp3.000)."""
    rounded = int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    sign = "-" if rounded < 0 else ""
    formatted = f"{abs(rounded):,}".replace(",", ".")
    return f"{sign}Rp{formatted}"
