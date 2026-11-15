"""Unit tests for transaction service logic."""

from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from app.domain.accounting.exceptions import InsufficientStockError
from app.domain.models import (
    AccountingExecutionResult,
    DomainJournalEntry,
    DomainJournalLine,
    DomainProduct,
    DomainStockMovement,
    TransactionStatus,
    TransactionType,
)
from app.services.transaction_service import (
    InvalidStatusError,
    TransactionNotFoundError,
    TransactionServiceError,
    confirm_transaction,
    reject_transaction,
)


def test_confirm_transaction_not_found():
    """Verify confirm_transaction raises TransactionNotFoundError when tx does not exist."""
    with patch("app.services.transaction_service.get_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        with patch(
            "app.services.transaction_service.TransactionRepository"
        ) as mock_repo_cls:
            mock_repo = mock_repo_cls.return_value
            mock_repo.get_by_id.return_value = None

            with pytest.raises(TransactionNotFoundError):
                confirm_transaction(999)


def test_confirm_transaction_invalid_status():
    """Verify confirm_transaction raises InvalidStatusError when status is not pending."""
    with patch("app.services.transaction_service.get_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        with patch(
            "app.services.transaction_service.TransactionRepository"
        ) as mock_repo_cls:
            mock_repo = mock_repo_cls.return_value
            mock_tx = type("MockTx", (), {"id": 1, "status": "confirmed"})()
            mock_repo.get_by_id.return_value = mock_tx

            with pytest.raises(InvalidStatusError):
                confirm_transaction(1)


def test_confirm_transaction_insufficient_stock_raises_service_error():
    """Verify confirm_transaction raises TransactionServiceError when stock is insufficient."""
    with patch("app.services.transaction_service.get_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        with (
            patch(
                "app.services.transaction_service.TransactionRepository"
            ) as mock_tx_repo_cls,
            patch(
                "app.services.transaction_service.ProductRepository"
            ) as mock_prod_repo_cls,
            patch(
                "app.services.transaction_service.execute_accounting_transaction"
            ) as mock_exec,
        ):

            mock_tx_repo = mock_tx_repo_cls.return_value
            mock_item = type(
                "MockItem",
                (),
                {"product_id": 1, "qty": 100, "unit_price": Decimal("3000.00")},
            )()
            mock_tx = type(
                "MockTx",
                (),
                {
                    "id": 10,
                    "status": TransactionStatus.PENDING.value,
                    "type": TransactionType.SALE.value,
                    "items": [mock_item],
                },
            )()
            mock_tx_repo.get_by_id.return_value = mock_tx

            mock_prod_repo = mock_prod_repo_cls.return_value
            mock_prod = DomainProduct(
                id=1,
                name="Pelet Jitu",
                category="bahan_umpan",
                unit="bungkus",
                sell_price=Decimal("3000.00"),
                avg_cost=Decimal("2500.00"),
                stock_qty=5,
                min_stock=1,
            )
            mock_prod_repo.get_by_ids.return_value = {1: mock_prod}

            mock_exec.side_effect = InsufficientStockError(
                "Stok Pelet Jitu tidak mencukupi"
            )

            with pytest.raises(TransactionServiceError) as exc_info:
                confirm_transaction(10)

            assert "tidak mencukupi" in str(exc_info.value)
            # Pastikan transaksi tidak disimpan sebagai confirmed
            assert mock_tx_repo.save_confirmed_transaction.call_count == 0


def test_confirm_transaction_success():
    """Verify confirm_transaction runs domain accounting and returns remaining stock."""
    with patch("app.services.transaction_service.get_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        with (
            patch(
                "app.services.transaction_service.TransactionRepository"
            ) as mock_tx_repo_cls,
            patch(
                "app.services.transaction_service.ProductRepository"
            ) as mock_prod_repo_cls,
            patch(
                "app.services.transaction_service.execute_accounting_transaction"
            ) as mock_exec,
        ):

            mock_tx_repo = mock_tx_repo_cls.return_value
            mock_item = type(
                "MockItem",
                (),
                {"product_id": 1, "qty": 2, "unit_price": Decimal("3000.00")},
            )()
            mock_tx = type(
                "MockTx",
                (),
                {
                    "id": 10,
                    "status": TransactionStatus.PENDING.value,
                    "type": TransactionType.SALE.value,
                    "items": [mock_item],
                },
            )()
            mock_tx_repo.get_by_id.return_value = mock_tx

            mock_prod_repo = mock_prod_repo_cls.return_value
            mock_prod = DomainProduct(
                id=1,
                name="Pelet Jitu",
                category="bahan_umpan",
                unit="bungkus",
                sell_price=Decimal("3000.00"),
                avg_cost=Decimal("2500.00"),
                stock_qty=10,
                min_stock=1,
            )
            mock_prod_repo.get_by_ids.return_value = {1: mock_prod}

            updated_prod = DomainProduct(
                id=1,
                name="Pelet Jitu",
                category="bahan_umpan",
                unit="bungkus",
                sell_price=Decimal("3000.00"),
                avg_cost=Decimal("2500.00"),
                stock_qty=8,
                min_stock=1,
            )
            mock_result = AccountingExecutionResult(
                updated_products=[updated_prod],
                stock_movements=[
                    DomainStockMovement(
                        product_id=1, qty_change=-2, unit_cost=Decimal("2500.00")
                    )
                ],
                journal_entry=DomainJournalEntry(
                    entry_date="2026-11-12",
                    description="Penjualan",
                    lines=[],
                ),
            )
            mock_exec.return_value = mock_result

            res = confirm_transaction(10)

            assert res["status"] == "confirmed"
            assert res["transaction_id"] == 10
            assert res["remaining_stock"][0]["stock_qty"] == 8
            assert mock_tx_repo.save_confirmed_transaction.call_count == 1


def test_reject_transaction_success_and_errors():
    """Verify reject_transaction updates status or raises error appropriately."""
    with patch("app.services.transaction_service.get_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        with patch(
            "app.services.transaction_service.TransactionRepository"
        ) as mock_tx_repo_cls:
            mock_tx_repo = mock_tx_repo_cls.return_value

            # Test not found
            mock_tx_repo.get_by_id.return_value = None
            with pytest.raises(TransactionNotFoundError):
                reject_transaction(99)

            # Test already rejected
            mock_tx = type("MockTx", (), {"id": 5, "status": "rejected"})()
            mock_tx_repo.get_by_id.return_value = mock_tx
            with pytest.raises(InvalidStatusError):
                reject_transaction(5)

            # Test success
            mock_tx_pending = type("MockTx", (), {"id": 7, "status": "pending"})()
            mock_tx_repo.get_by_id.return_value = mock_tx_pending
            res = reject_transaction(7)
            assert res["status"] == "rejected"
            assert mock_tx_repo.reject_transaction.call_count == 1


def test_confirm_and_reject_lock_transaction_row_for_update():
    """Verify confirm and reject acquire a FOR UPDATE lock when loading the transaction."""
    with patch("app.services.transaction_service.get_session"):
        with patch(
            "app.services.transaction_service.TransactionRepository"
        ) as mock_tx_repo_cls:
            mock_tx_repo = mock_tx_repo_cls.return_value
            mock_tx_repo.get_by_id.return_value = type(
                "MockTx", (), {"id": 11, "status": "confirmed"}
            )()

            with pytest.raises(InvalidStatusError):
                confirm_transaction(11)
            mock_tx_repo.get_by_id.assert_called_once_with(11, for_update=True)

            mock_tx_repo.get_by_id.reset_mock()
            mock_tx_repo.get_by_id.return_value = type(
                "MockTx", (), {"id": 12, "status": "rejected"}
            )()

            with pytest.raises(InvalidStatusError):
                reject_transaction(12)
            mock_tx_repo.get_by_id.assert_called_once_with(12, for_update=True)
