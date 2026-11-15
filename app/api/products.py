"""Products API router for browsing store catalog and stock by category."""

from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel

from app.api.common import run_or_bad_request
from app.api.security import verify_api_key
from app.services.product_service import ProductService

router = APIRouter(
    prefix="/products",
    tags=["Products"],
    dependencies=[Depends(verify_api_key)],
)


class ProductItemSchema(BaseModel):
    """Schema describing a single store product entry."""

    product_id: int | None
    name: str
    category: str
    unit: str
    sell_price: Decimal
    sell_price_formatted: str
    stock_qty: int
    min_stock: int


class ProductListResponse(BaseModel):
    """Response schema for GET /products."""

    count: int
    category: str | None
    items: list[ProductItemSchema]
    summary_text: str


@router.get(
    "",
    response_model=ProductListResponse,
    status_code=status.HTTP_200_OK,
    summary="List store products with optional category filtering",
)
def get_products(
    category: str | None = Query(
        default=None,
        description="Filter kategori produk ('peralatan' atau 'bahan_umpan', opsional)",
    ),
) -> ProductListResponse:
    """Return store products sorted by ID along with a formatted text summary."""
    data = run_or_bad_request(lambda: ProductService().list_products(category=category))
    return ProductListResponse(**data)
