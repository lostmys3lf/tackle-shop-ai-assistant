"""End-to-end integration tests using in-memory database."""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from app.domain.accounting.rules import execute_accounting_transaction
from app.domain.models import (
    AccountCode,
    AccountType,
    DomainTransactionItem,
    TransactionExecutionContext,
    TransactionStatus,
    TransactionType,
)
from app.infrastructure.db.models import Account, Base, Product, Transaction
from app.infrastructure.db.repositories import (
    ProductRepository,
    TransactionRepository,
)
from app.services.transaction_service import (
    InvalidStatusError,
    confirm_transaction,
    reject_transaction,
)


@pytest.fixture
def in_memory_session():
    """Create a temporary SQLite in-memory database with tables initialized."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    # Seed core accounts
    accounts = [
        Account(code=AccountCode.CASH.value, name="Kas", type=AccountType.ASSET.value),
        Account(
            code=AccountCode.INVENTORY.value,
            name="Persediaan Barang Dagang",
            type=AccountType.ASSET.value,
        ),
        Account(
            code=AccountCode.OWNER_EQUITY.value,
            name="Modal Pemilik",
            type=AccountType.EQUITY.value,
        ),
        Account(
            code=AccountCode.SALES_REVENUE.value,
            name="Penjualan",
            type=AccountType.REVENUE.value,
        ),
        Account(
            code=AccountCode.COGS.value,
            name="Harga Pokok Penjualan",
            type=AccountType.EXPENSE.value,
        ),
    ]
    session.add_all(accounts)

    # Seed sample product
    product = Product(
        name="Pelet Jitu",
        category="bahan_umpan",
        unit="bungkus",
        sell_price=Decimal("3000.00"),
        avg_cost=Decimal("2500.00"),
        stock_qty=10,
        min_stock=2,
    )
    session.add(product)
    session.commit()

    yield session
    session.close()


def test_end_to_end_sale_transaction_and_confirm(in_memory_session: Session):
    """Verify pending sale creation, confirm execution, stock deduction, and balanced journal."""
    tx_repo = TransactionRepository(in_memory_session)
    prod_repo = ProductRepository(in_memory_session)

    # 1. Chat penjualan menghasilkan transaksi pending
    items = [{"product_id": 1, "qty": 3, "unit_price": Decimal("3000.00")}]
    tx = tx_repo.create_pending_transaction(
        tx_type=TransactionType.SALE.value,
        source="chat",
        raw_text="laku pelet jitu 3 bungkus",
        items=items,
    )
    in_memory_session.commit()

    assert tx.id is not None
    assert tx.status == TransactionStatus.PENDING.value

    # 2. Confirm menjalankan logika akuntansi Task 4 dan mengembalikan sisa stok
    products_by_id = prod_repo.get_by_ids([1])
    domain_items = [
        DomainTransactionItem(
            product_id=1,
            qty=3,
            unit_price=Decimal("3000.00"),
        )
    ]
    context = TransactionExecutionContext(
        tx_type=tx.type,
        tx_status=TransactionStatus.CONFIRMED,
        products_by_id=products_by_id,
        items=domain_items,
        transaction_id=tx.id,
        entry_date=date.today(),
    )

    result = execute_accounting_transaction(context)
    assert result is not None
    assert len(result.updated_products) == 1
    assert result.updated_products[0].stock_qty == 7  # 10 - 3

    # Simpan hasil akuntansi
    tx_repo.save_confirmed_transaction(tx.id, result)
    in_memory_session.commit()

    # Verifikasi perubahan di database
    updated_orm_tx = tx_repo.get_by_id(tx.id)
    assert updated_orm_tx.status == TransactionStatus.CONFIRMED.value
    assert updated_orm_tx.confirmed_at is not None

    updated_orm_prod = in_memory_session.get(Product, 1)
    assert updated_orm_prod.stock_qty == 7

    # Verifikasi jurnal tersimpan dan seimbang
    assert len(updated_orm_tx.journal_entries) == 1
    entry = updated_orm_tx.journal_entries[0]
    total_debit = sum(line.debit for line in entry.lines)
    total_credit = sum(line.credit for line in entry.lines)
    assert total_debit == total_credit


def test_end_to_end_reject_transaction(in_memory_session: Session):
    """Verify reject transaction updates status without altering stock or journals."""
    tx_repo = TransactionRepository(in_memory_session)

    items = [{"product_id": 1, "qty": 2, "unit_price": Decimal("3000.00")}]
    tx = tx_repo.create_pending_transaction(
        tx_type=TransactionType.SALE.value,
        source="chat",
        raw_text="laku pelet jitu 2 bungkus",
        items=items,
    )
    in_memory_session.commit()

    tx_repo.reject_transaction(tx.id)
    in_memory_session.commit()

    rejected_tx = tx_repo.get_by_id(tx.id)
    assert rejected_tx.status == TransactionStatus.REJECTED.value

    # Stok produk tidak berubah
    prod = in_memory_session.get(Product, 1)
    assert prod.stock_qty == 10
    assert len(rejected_tx.journal_entries) == 0
    assert len(rejected_tx.stock_movements) == 0


def test_confirm_already_confirmed_transaction_raises_invalid_status(
    in_memory_session: Session,
):
    """Verify double-confirming hits InvalidStatusError and does not apply stock/journal twice."""
    tx_repo = TransactionRepository(in_memory_session)
    tx = tx_repo.create_pending_transaction(
        tx_type=TransactionType.SALE.value,
        source="chat",
        raw_text="laku pelet jitu 3 bungkus",
        items=[{"product_id": 1, "qty": 3, "unit_price": Decimal("3000.00")}],
    )
    in_memory_session.commit()

    @contextmanager
    def _fake_get_session():
        try:
            yield in_memory_session
            in_memory_session.commit()
        except Exception:
            in_memory_session.rollback()
            raise

    with patch(
        "app.services.transaction_service.get_session", side_effect=_fake_get_session
    ):
        first = confirm_transaction(tx.id)
        assert first["status"] == TransactionStatus.CONFIRMED.value
        assert first["remaining_stock"][0]["stock_qty"] == 7

        # Konfirmasi ulang pada transaksi yang sudah confirmed harus ditolak
        with pytest.raises(InvalidStatusError):
            confirm_transaction(tx.id)

        # Reject pada transaksi yang sudah confirmed juga harus ditolak
        with pytest.raises(InvalidStatusError):
            reject_transaction(tx.id)

    in_memory_session.expire_all()
    prod = in_memory_session.get(Product, 1)
    assert prod.stock_qty == 7
    updated_tx = tx_repo.get_by_id(tx.id)
    assert len(updated_tx.journal_entries) == 1
    assert len(updated_tx.stock_movements) == 1


def test_transaction_for_update_query_emits_postgres_row_lock():
    """Verify SQLAlchemy with_for_update emits SELECT ... FOR UPDATE on PostgreSQL."""
    captured_sql: list[str] = []

    class _SpySession:
        def scalars(self, stmt):
            captured_sql.append(str(stmt.compile(dialect=postgresql.dialect())))
            return type("_EmptyResult", (), {"first": staticmethod(lambda: None)})()

    repo = TransactionRepository(_SpySession())  # type: ignore[arg-type]
    repo.get_by_id(1, for_update=True)
    assert len(captured_sql) == 1
    assert "FOR UPDATE" in captured_sql[0]

    repo.get_by_id(1, for_update=False)
    assert "FOR UPDATE" not in captured_sql[1]
