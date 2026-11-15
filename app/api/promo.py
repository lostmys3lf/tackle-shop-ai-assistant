"""Promo API router for generating slow-moving product captions and bundle suggestions."""

from decimal import Decimal

from fastapi import APIRouter, Body, Depends, status
from pydantic import BaseModel, Field

from app.api.common import run_or_bad_request
from app.api.security import verify_api_key
from app.services.promo_service import (
    DEFAULT_SLOW_LIMIT,
    DEFAULT_WINDOW_DAYS,
    PromoService,
    SlowMovingPromoOptions,
)

router = APIRouter(
    prefix="/promo",
    tags=["Promo"],
    dependencies=[Depends(verify_api_key)],
)


class SlowMovingPromoRequest(BaseModel):
    """Optional request parameters for POST /promo/slow-moving."""

    window_days: int = Field(default=DEFAULT_WINDOW_DAYS, ge=1, le=365)
    limit: int = Field(default=DEFAULT_SLOW_LIMIT, ge=1, le=10)


class SlowMovingProductSchema(BaseModel):
    """Schema describing a slow-moving stocked product."""

    product_id: int
    product_name: str
    category: str
    unit: str
    stock_qty: int
    sell_price: Decimal
    sell_price_formatted: str
    sold_qty_window: int
    last_sold_date: str | None
    days_since_last_sale: int | None
    stock_value: Decimal
    stock_value_formatted: str


class BundleSuggestionSchema(BaseModel):
    """Schema describing a deterministically priced promo bundle."""

    slow_product_id: int
    slow_product_name: str
    partner_product_id: int
    partner_product_name: str
    normal_total_price: Decimal
    normal_total_price_formatted: str
    bundle_price: Decimal
    bundle_price_formatted: str
    discount_amount: Decimal
    discount_amount_formatted: str
    discount_percent: int


class SlowMovingPromoResponse(BaseModel):
    """Response schema for POST /promo/slow-moving."""

    has_slow_moving: bool
    window_days: int
    slow_moving_items: list[SlowMovingProductSchema]
    bundle_suggestions: list[BundleSuggestionSchema]
    caption: str
    bundling_advice: str
    summary_text: str


@router.post(
    "/slow-moving",
    response_model=SlowMovingPromoResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate promo captions and bundling advice for slow-moving inventory",
)
def generate_slow_moving_promo(
    payload: SlowMovingPromoRequest = Body(default_factory=SlowMovingPromoRequest),
) -> SlowMovingPromoResponse:
    """Identify slow-moving items and return verified promo caption and bundles."""
    options = SlowMovingPromoOptions(
        window_days=payload.window_days,
        limit=payload.limit,
    )
    data = run_or_bad_request(
        lambda: PromoService().generate_slow_moving_promo(options=options)
    )
    return SlowMovingPromoResponse(**data)
