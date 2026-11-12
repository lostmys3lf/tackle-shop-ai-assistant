"""Transaction service orchestrating confirmation, rejection, and accounting execution."""

from datetime import date
from typing import Any

from app.domain.accounting.exceptions import InsufficientStockError
from app.domain.accounting.rules import execute_accounting_transaction
from app.domain.models import (
    DomainProduct,
    DomainTransactionItem,
    TransactionExecutionContext,
    TransactionStatus,
    TransactionType,
)
from app.infrastructure.db.connection import get_session
from app.infrastructure.db.repositories import (
    ProductRepository,
    TransactionRepository,
)


class TransactionServiceError(Exception):
    """Base exception for transaction service errors."""


class TransactionNotFoundError(TransactionServiceError):
    """Raised when the specified transaction ID does not exist."""


class InvalidStatusError(TransactionServiceError):
    """Raised when a transaction is not in pending status for confirmation or rejection."""


def _format_confirmation_summary(
    tx_type: str,
    updated_products: list[DomainProduct],
) -> str:
    """Format human-readable summary of confirmed transaction with remaining stock."""
    type_name = "Penjualan" if tx_type == TransactionType.SALE.value else "Pembelian"
    lines = [f"{type_name} berhasil dicatat dan dikonfirmasi.", "Sisa stok saat ini:"]
    for p in updated_products:
        lines.append(f"- {p.name}: {p.stock_qty} {p.unit}")
    return "\n".join(lines)


def confirm_transaction(tx_id: int) -> dict[str, Any]:
    """Execute domain accounting logic for a pending transaction and commit updates."""
    with get_session() as session:
        tx_repo = TransactionRepository(session)
        prod_repo = ProductRepository(session)

        tx = tx_repo.get_by_id(tx_id)
        if tx is None:
            raise TransactionNotFoundError(f"Transaksi ID {tx_id} tidak ditemukan.")

        if tx.status != TransactionStatus.PENDING.value:
            raise InvalidStatusError(
                f"Transaksi ID {tx_id} tidak dapat dikonfirmasi karena berstatus '{tx.status}'."
            )

        product_ids = [item.product_id for item in tx.items]
        products_by_id = prod_repo.get_by_ids(product_ids)

        domain_items = [
            DomainTransactionItem(
                product_id=item.product_id,
                qty=item.qty,
                unit_price=item.unit_price,
            )
            for item in tx.items
        ]

        context = TransactionExecutionContext(
            tx_type=tx.type,
            tx_status=TransactionStatus.CONFIRMED,
            products_by_id=products_by_id,
            items=domain_items,
            transaction_id=tx.id,
            entry_date=date.today(),
        )

        try:
            result = execute_accounting_transaction(context)
        except InsufficientStockError as exc:
            # Penjualan melebihi stok ditolak; status tetap pending
            raise TransactionServiceError(str(exc)) from exc

        if result is None:
            raise TransactionServiceError("Gagal memproses akuntansi transaksi.")

        tx_repo.save_confirmed_transaction(tx.id, result)

        remaining_stock = [
            {
                "product_id": p.id,
                "product_name": p.name,
                "stock_qty": p.stock_qty,
                "unit": p.unit,
            }
            for p in result.updated_products
        ]

        summary_text = _format_confirmation_summary(tx.type, result.updated_products)

        return {
            "transaction_id": tx.id,
            "status": TransactionStatus.CONFIRMED.value,
            "summary_text": summary_text,
            "remaining_stock": remaining_stock,
        }


def reject_transaction(tx_id: int) -> dict[str, Any]:
    """Reject a pending transaction without executing accounting or stock changes."""
    with get_session() as session:
        tx_repo = TransactionRepository(session)

        tx = tx_repo.get_by_id(tx_id)
        if tx is None:
            raise TransactionNotFoundError(f"Transaksi ID {tx_id} tidak ditemukan.")

        if tx.status != TransactionStatus.PENDING.value:
            raise InvalidStatusError(
                f"Transaksi ID {tx_id} tidak dapat dibatalkan karena berstatus '{tx.status}'."
            )

        tx_repo.reject_transaction(tx.id)

        return {
            "transaction_id": tx.id,
            "status": TransactionStatus.REJECTED.value,
            "message": f"Transaksi ID {tx.id} berhasil dibatalkan.",
        }
