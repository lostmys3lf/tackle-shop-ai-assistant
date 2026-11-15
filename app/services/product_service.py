"""Product catalog service for listing store products and formatted summaries."""

# pylint: disable=too-few-public-methods

from typing import Any

from sqlalchemy.orm import Session

from app.domain.models import DomainProduct
from app.infrastructure.db.connection import get_session
from app.infrastructure.db.repositories import ProductRepository
from app.services.formatting import format_rupiah, serialize_product_summary

CATEGORY_LABELS: dict[str, str] = {
    "peralatan": "Peralatan",
    "bahan_umpan": "Bahan Umpan",
}


def normalize_product_category(category: str | None) -> str | None:
    """Validate and normalize optional product category query parameter."""
    if category is None:
        return None
    normalized = category.strip().lower()
    if not normalized:
        return None
    if normalized not in CATEGORY_LABELS:
        raise ValueError(
            "Kategori produk tidak valid. Gunakan 'peralatan' atau 'bahan_umpan'."
        )
    return normalized


def _build_product_list_summary(
    products: list[DomainProduct],
    category: str | None,
) -> str:
    """Format store product list with current stock and selling price."""
    heading = (
        f"Daftar Produk ({CATEGORY_LABELS[category]}):"
        if category is not None
        else "Daftar Semua Produk Toko:"
    )
    if not products:
        return f"{heading}\nBelum ada produk terdaftar."

    lines = [heading]
    for product in products:
        low_note = " (menipis)" if product.stock_qty <= product.min_stock else ""
        lines.append(
            f"- {product.name}: stok {product.stock_qty} {product.unit}{low_note} "
            f"| Harga {format_rupiah(product.sell_price)}"
        )
    return "\n".join(lines)


class ProductService:
    """Service layer returning store product lists with optional category filtering."""

    def __init__(self, product_repo: Any | None = None) -> None:
        """Initialize product service with optional injected repository for testing."""
        self.product_repo = product_repo

    def list_products(
        self,
        category: str | None = None,
        session: Session | None = None,
    ) -> dict[str, Any]:
        """Return registered products, optionally filtered by category."""
        resolved_category = normalize_product_category(category)

        if self.product_repo is not None:
            items: list[DomainProduct] = self.product_repo.get_all(
                category=resolved_category
            )
        elif session is not None:
            items = ProductRepository(session).get_all(category=resolved_category)
        else:
            with get_session() as db_session:
                items = ProductRepository(db_session).get_all(
                    category=resolved_category
                )

        return {
            "count": len(items),
            "category": resolved_category,
            "items": [
                {
                    **serialize_product_summary(item),
                    "sell_price": item.sell_price,
                    "sell_price_formatted": format_rupiah(item.sell_price),
                }
                for item in items
            ],
            "summary_text": _build_product_list_summary(items, resolved_category),
        }
