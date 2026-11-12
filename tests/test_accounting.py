"""Unit tests for pure accounting domain logic."""

from datetime import date
from decimal import Decimal
import pytest

from app.domain.accounting.exceptions import (
    InsufficientStockError,
    MissingPriceError,
    UnbalancedJournalError,
)
from app.domain.accounting.rules import (
    calculate_new_avg_cost,
    execute_accounting_transaction,
    process_purchase_transaction,
    process_sale_transaction,
    validate_journal_balance,
)
from app.domain.models import (
    AccountCode,
    DomainJournalEntry,
    DomainJournalLine,
    DomainProduct,
    DomainTransactionItem,
    TransactionExecutionContext,
    TransactionStatus,
    TransactionType,
)


def _build_sample_product(
    product_id: int = 1,
    name: str = "Senar Test",
    stock_qty: int = 10,
    avg_cost: Decimal = Decimal("50000.00"),
    sell_price: Decimal = Decimal("70000.00"),
) -> DomainProduct:
    """Helper to construct a test DomainProduct instance."""
    return DomainProduct(
        id=product_id,
        name=name,
        category="peralatan",
        unit="gulung",
        sell_price=sell_price,
        avg_cost=avg_cost,
        stock_qty=stock_qty,
        min_stock=3,
    )


def test_spec_cogs_weighted_average_cost_example():
    """Verify exact weighted average cost calculation from spec section 8.

    Spec: 'stok 10 dengan avg 50.000, beli 10 seharga 60.000, maka avg baru 55.000. Jual 4, HPP = 220.000.'
    """
    # 1. Beli 10 @ 60.000
    new_avg = calculate_new_avg_cost(
        old_stock=10,
        old_avg_cost=Decimal("50000.00"),
        purchase_qty=10,
        purchase_unit_price=Decimal("60000.00"),
    )
    assert new_avg == Decimal("55000.00")

    product = _build_sample_product(stock_qty=20, avg_cost=new_avg)
    products_map = {product.id: product}

    # 2. Jual 4 @ harga jual
    sale_item = DomainTransactionItem(
        product_id=product.id,
        qty=4,
        unit_price=Decimal("70000.00"),
    )
    result = process_sale_transaction(
        products_by_id=products_map,
        items=[sale_item],
        transaction_id=1,
        entry_date=date.today(),
    )

    # Verifikasi sisa stok
    assert result.updated_products[0].stock_qty == 16
    # Verifikasi stock movement unit_cost = avg_cost (55.000) dan qty = -4
    assert result.stock_movements[0].qty_change == -4
    assert result.stock_movements[0].unit_cost == Decimal("55000.00")

    # Verifikasi nilai HPP = 4 * 55.000 = 220.000
    cogs_lines = [
        line
        for line in result.journal_entry.lines
        if line.account_code == AccountCode.COGS.value
    ]
    assert len(cogs_lines) == 1
    assert cogs_lines[0].debit == Decimal("220000.00")


def test_purchase_journal_is_balanced():
    """Ensure purchase transactions create balanced journals (Debit Persediaan == Kredit Kas)."""
    product = _build_sample_product(stock_qty=5, avg_cost=Decimal("20000.00"))
    products_map = {product.id: product}

    item = DomainTransactionItem(
        product_id=product.id,
        qty=10,
        unit_price=Decimal("25000.00"),
    )
    result = process_purchase_transaction(
        products_by_id=products_map,
        items=[item],
        transaction_id=10,
        entry_date=date.today(),
    )

    lines = result.journal_entry.lines
    total_debit = sum((line.debit for line in lines), Decimal("0.00"))
    total_credit = sum((line.credit for line in lines), Decimal("0.00"))

    assert total_debit == total_credit == Decimal("250000.00")
    assert result.updated_products[0].stock_qty == 15
    # Cek rata-rata baru: (5 * 20000 + 10 * 25000) / 15 = 350000 / 15 = 23333.33
    assert result.updated_products[0].avg_cost == Decimal("23333.33")


def test_sale_journal_is_balanced():
    """Ensure sale transactions create balanced journals (Kas + COGS == Sales + Inventory)."""
    product = _build_sample_product(
        stock_qty=10,
        avg_cost=Decimal("30000.00"),
        sell_price=Decimal("45000.00"),
    )
    products_map = {product.id: product}

    item = DomainTransactionItem(
        product_id=product.id,
        qty=2,
        unit_price=Decimal("45000.00"),
    )
    result = process_sale_transaction(
        products_by_id=products_map,
        items=[item],
        transaction_id=20,
        entry_date=date.today(),
    )

    lines = result.journal_entry.lines
    total_debit = sum((line.debit for line in lines), Decimal("0.00"))
    total_credit = sum((line.credit for line in lines), Decimal("0.00"))

    # Total debit = 90.000 (Kas) + 60.000 (HPP) = 150.000
    # Total credit = 90.000 (Penjualan) + 60.000 (Persediaan) = 150.000
    assert total_debit == total_credit == Decimal("150000.00")
    assert result.updated_products[0].stock_qty == 8


def test_sale_exceeding_stock_is_rejected():
    """Ensure sales with quantity greater than available stock are rejected."""
    product = _build_sample_product(stock_qty=3)
    products_map = {product.id: product}

    item = DomainTransactionItem(
        product_id=product.id,
        qty=4,
        unit_price=Decimal("50000.00"),
    )
    with pytest.raises(InsufficientStockError, match="tidak mencukupi"):
        process_sale_transaction(
            products_by_id=products_map,
            items=[item],
            transaction_id=30,
            entry_date=date.today(),
        )


def test_pending_and_rejected_transactions_do_not_alter_stock_or_journals():
    """Ensure pending or rejected transactions return None without altering inventory or producing journals."""
    product = _build_sample_product(stock_qty=10, avg_cost=Decimal("50000.00"))
    products_map = {product.id: product}

    item = DomainTransactionItem(
        product_id=product.id,
        qty=2,
        unit_price=Decimal("70000.00"),
    )

    # 1. Transaksi pending
    pending_ctx = TransactionExecutionContext(
        tx_type=TransactionType.SALE,
        tx_status=TransactionStatus.PENDING,
        products_by_id=products_map,
        items=[item],
        transaction_id=100,
        entry_date=date.today(),
    )
    pending_result = execute_accounting_transaction(pending_ctx)
    assert pending_result is None
    assert product.stock_qty == 10  # Stok tidak berubah

    # 2. Transaksi rejected
    rejected_ctx = TransactionExecutionContext(
        tx_type=TransactionType.SALE,
        tx_status=TransactionStatus.REJECTED,
        products_by_id=products_map,
        items=[item],
        transaction_id=101,
        entry_date=date.today(),
    )
    rejected_result = execute_accounting_transaction(rejected_ctx)
    assert rejected_result is None
    assert product.stock_qty == 10  # Stok tetap tidak berubah


def test_sale_with_empty_unit_price_uses_product_sell_price():
    """Ensure sale without explicit unit price falls back to the product's sell_price."""
    product = _build_sample_product(
        stock_qty=5,
        avg_cost=Decimal("20000.00"),
        sell_price=Decimal("35000.00"),
    )
    products_map = {product.id: product}

    # unit_price = 0 / None
    item = DomainTransactionItem(
        product_id=product.id,
        qty=2,
        unit_price=Decimal("0.00"),
    )
    result = process_sale_transaction(
        products_by_id=products_map,
        items=[item],
        transaction_id=40,
        entry_date=date.today(),
    )

    # Pendapatan penjualan harus dihitung dari 2 * 35.000 = 70.000
    cash_line = next(
        line
        for line in result.journal_entry.lines
        if line.account_code == AccountCode.CASH.value
    )
    assert cash_line.debit == Decimal("70000.00")


def test_purchase_with_missing_price_raises_error():
    """Ensure purchase with missing or non-positive price raises MissingPriceError."""
    product = _build_sample_product()
    products_map = {product.id: product}

    item = DomainTransactionItem(
        product_id=product.id,
        qty=5,
        unit_price=Decimal("0.00"),
    )
    with pytest.raises(MissingPriceError, match="price is required"):
        process_purchase_transaction(
            products_by_id=products_map,
            items=[item],
            transaction_id=50,
            entry_date=date.today(),
        )


def test_unbalanced_journal_raises_error():
    """Ensure validate_journal_balance raises UnbalancedJournalError when debits != credits."""
    unbalanced_entry = DomainJournalEntry(
        entry_date=date.today(),
        description="Jurnal tidak seimbang",
        lines=[
            DomainJournalLine(
                account_code="1-101",
                debit=Decimal("10000.00"),
                credit=Decimal("0.00"),
            ),
            DomainJournalLine(
                account_code="4-101",
                debit=Decimal("0.00"),
                credit=Decimal("9000.00"),
            ),
        ],
    )
    with pytest.raises(UnbalancedJournalError, match="Unbalanced journal entry"):
        validate_journal_balance(unbalanced_entry)
