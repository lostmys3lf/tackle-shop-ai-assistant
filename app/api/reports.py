"""Reports API router for low-stock alerts, sales summaries, and profit & loss."""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.security import verify_api_key
from app.services.report_service import ReportService

router = APIRouter(
    prefix="/reports",
    tags=["Reports"],
    dependencies=[Depends(verify_api_key)],
)


class LowStockItemSchema(BaseModel):
    """Schema describing a single low-stock product entry."""

    product_id: int | None
    name: str
    category: str
    unit: str
    stock_qty: int
    min_stock: int


class LowStockReportResponse(BaseModel):
    """Response schema for GET /reports/low-stock."""

    count: int
    has_low_stock: bool
    items: list[LowStockItemSchema]
    summary_text: str


class TopProductItemSchema(BaseModel):
    """Schema describing a top-selling product in a period."""

    product_id: int
    product_name: str
    total_qty: int
    total_revenue: Decimal
    total_revenue_formatted: str
    category: str
    unit: str


class SalesReportResponse(BaseModel):
    """Response schema for GET /reports/sales."""

    start_date: date
    end_date: date
    total_sales: Decimal
    total_sales_formatted: str
    transaction_count: int
    total_items_sold: int
    top_products: list[TopProductItemSchema]
    summary_text: str


class ProfitLossReportResponse(BaseModel):
    """Response schema for GET /reports/profit-loss."""

    month: str
    total_sales: Decimal
    total_sales_formatted: str
    total_cogs: Decimal
    total_cogs_formatted: str
    gross_profit: Decimal
    gross_profit_formatted: str
    summary_text: str


@router.get(
    "/low-stock",
    response_model=LowStockReportResponse,
    status_code=status.HTTP_200_OK,
    summary="List products whose stock is at or below min_stock",
)
def get_low_stock_report() -> LowStockReportResponse:
    """Return products with stock_qty <= min_stock and formatted alert text."""
    data = ReportService().get_low_stock_report()
    return LowStockReportResponse(**data)


@router.get(
    "/sales",
    response_model=SalesReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get sales total, transaction count, and best-selling products for period",
)
def get_sales_report(
    start: date | None = Query(
        default=None, description="Tanggal awal (YYYY-MM-DD, opsional)"
    ),
    end: date | None = Query(
        default=None, description="Tanggal akhir (YYYY-MM-DD, opsional)"
    ),
) -> SalesReportResponse:
    """Return aggregated confirmed sales and best-selling items in date range."""
    try:
        data = ReportService().get_sales_report(start=start, end=end)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return SalesReportResponse(**data)


@router.get(
    "/profit-loss",
    response_model=ProfitLossReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get monthly sales, COGS, and gross profit",
)
def get_profit_loss_report(
    month: str | None = Query(
        default=None, description="Bulan laporan (YYYY-MM, opsional)"
    ),
) -> ProfitLossReportResponse:
    """Return monthly sales revenue, COGS, and gross profit."""
    try:
        data = ReportService().get_profit_loss_report(month=month)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return ProfitLossReportResponse(**data)
