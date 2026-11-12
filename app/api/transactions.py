"""Transactions API router for confirming and rejecting pending transactions."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.security import verify_api_key
from app.services.transaction_service import (
    InvalidStatusError,
    TransactionNotFoundError,
    TransactionServiceError,
    confirm_transaction,
    reject_transaction,
)

router = APIRouter(
    prefix="/transactions",
    tags=["Transactions"],
    dependencies=[Depends(verify_api_key)],
)


class RemainingStockItem(BaseModel):
    """Schema for individual remaining stock item."""

    product_id: int
    product_name: str
    stock_qty: int
    unit: str


class TransactionConfirmResponse(BaseModel):
    """Schema for transaction confirmation response."""

    transaction_id: int
    status: str
    summary_text: str
    remaining_stock: list[RemainingStockItem]


class TransactionRejectResponse(BaseModel):
    """Schema for transaction rejection response."""

    transaction_id: int
    status: str
    message: str


@router.post(
    "/{tx_id}/confirm",
    response_model=TransactionConfirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirm a pending transaction and execute accounting logic",
)
def confirm_pending_transaction(tx_id: int) -> TransactionConfirmResponse:
    """Confirm pending transaction, apply double-entry journal, update stock."""
    try:
        result = confirm_transaction(tx_id)
        return TransactionConfirmResponse(**result)
    except TransactionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except (InvalidStatusError, TransactionServiceError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/{tx_id}/reject",
    response_model=TransactionRejectResponse,
    status_code=status.HTTP_200_OK,
    summary="Reject and cancel a pending transaction",
)
def reject_pending_transaction(tx_id: int) -> TransactionRejectResponse:
    """Reject and cancel pending transaction without modifying stock or journals."""
    try:
        result = reject_transaction(tx_id)
        return TransactionRejectResponse(**result)
    except TransactionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except InvalidStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
