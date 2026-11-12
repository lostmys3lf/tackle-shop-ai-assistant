"""Pure business logic for stock, weighted average cost (HPP), and double-entry journals."""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.domain.accounting.exceptions import (
    InsufficientStockError,
    MissingPriceError,
    UnbalancedJournalError,
)
from app.domain.models import (
    AccountCode,
    AccountingExecutionResult,
    DomainJournalEntry,
    DomainJournalLine,
    DomainProduct,
    DomainStockMovement,
    DomainTransactionItem,
    TransactionExecutionContext,
    TransactionStatus,
    TransactionType,
)

CURRENCY_PRECISION = Decimal("0.01")


def round_currency(amount: Decimal) -> Decimal:
    """Round monetary amount to standard two decimal places using ROUND_HALF_UP."""
    return amount.quantize(CURRENCY_PRECISION, rounding=ROUND_HALF_UP)


def calculate_new_avg_cost(
    old_stock: int,
    old_avg_cost: Decimal,
    purchase_qty: int,
    purchase_unit_price: Decimal,
) -> Decimal:
    """Calculate the new weighted average cost per unit upon inventory purchase.

    Formula:
    new_cost = (old_stock * old_cost + purchase_qty * purchase_price) / (old_stock + purchase_qty)
    """
    total_qty = old_stock + purchase_qty
    if total_qty <= 0:
        return round_currency(purchase_unit_price)

    if old_stock <= 0:
        # Jika stok lama nol atau negatif, harga pokok baru sepenuhnya dari pembelian baru
        return round_currency(purchase_unit_price)

    old_total_value = Decimal(old_stock) * old_avg_cost
    new_purchase_value = Decimal(purchase_qty) * purchase_unit_price
    new_avg = (old_total_value + new_purchase_value) / Decimal(total_qty)
    return round_currency(new_avg)


def validate_journal_balance(entry: DomainJournalEntry) -> None:
    """Validate that total debits equal total credits across all lines in a journal entry."""
    total_debit = sum((line.debit for line in entry.lines), Decimal("0.00"))
    total_credit = sum((line.credit for line in entry.lines), Decimal("0.00"))

    # Jurnal akuntansi wajib seimbang hingga 2 digit desimal
    if round_currency(total_debit) != round_currency(total_credit):
        raise UnbalancedJournalError(
            f"Unbalanced journal entry: total debit {total_debit} != total credit {total_credit}"
        )


def process_opening_transaction(
    products: list[DomainProduct],
    transaction_id: int | None,
    entry_date: date,
) -> AccountingExecutionResult:
    """Process opening inventory seed into stock movements and initial journal entry.

    Debit: Persediaan Barang Dagang (1-301) sebesar total nilai persediaan.
    Kredit: Modal Pemilik (3-101) sebesar total nilai persediaan.
    """
    stock_movements: list[DomainStockMovement] = []
    updated_products: list[DomainProduct] = []
    total_inventory_value = Decimal("0.00")

    for product in products:
        item_total = round_currency(Decimal(product.stock_qty) * product.avg_cost)
        total_inventory_value += item_total

        # Buat pencatatan pergerakan stok awal
        stock_movements.append(
            DomainStockMovement(
                product_id=product.id if product.id is not None else 0,
                transaction_id=transaction_id,
                qty_change=product.stock_qty,
                unit_cost=product.avg_cost,
            )
        )
        updated_products.append(product)

    journal_entry = DomainJournalEntry(
        entry_date=entry_date,
        description="Saldo awal persediaan barang dagang",
        transaction_id=transaction_id,
        lines=[
            DomainJournalLine(
                account_code=AccountCode.INVENTORY.value,
                debit=total_inventory_value,
                credit=Decimal("0.00"),
            ),
            DomainJournalLine(
                account_code=AccountCode.OWNER_EQUITY.value,
                debit=Decimal("0.00"),
                credit=total_inventory_value,
            ),
        ],
    )

    validate_journal_balance(journal_entry)

    return AccountingExecutionResult(
        updated_products=updated_products,
        stock_movements=stock_movements,
        journal_entry=journal_entry,
    )


def process_purchase_transaction(
    products_by_id: dict[int, DomainProduct],
    items: list[DomainTransactionItem],
    transaction_id: int | None,
    entry_date: date,
) -> AccountingExecutionResult:
    """Process a confirmed purchase transaction.

    Debit: Persediaan Barang Dagang (1-301) sebesar qty * unit_price.
    Kredit: Kas (1-101) sebesar qty * unit_price.
    """
    stock_movements: list[DomainStockMovement] = []
    updated_products: list[DomainProduct] = []
    total_purchase_amount = Decimal("0.00")

    for item in items:
        if item.unit_price is None or item.unit_price <= Decimal("0.00"):
            raise MissingPriceError(
                f"Purchase unit price is required for product ID {item.product_id}"
            )

        product = products_by_id.get(item.product_id)
        if product is None:
            raise KeyError(f"Product ID {item.product_id} not found")

        item_total = round_currency(Decimal(item.qty) * item.unit_price)
        total_purchase_amount += item_total

        new_avg_cost = calculate_new_avg_cost(
            old_stock=product.stock_qty,
            old_avg_cost=product.avg_cost,
            purchase_qty=item.qty,
            purchase_unit_price=item.unit_price,
        )
        new_stock_qty = product.stock_qty + item.qty

        product.avg_cost = new_avg_cost
        product.stock_qty = new_stock_qty
        updated_products.append(product)

        stock_movements.append(
            DomainStockMovement(
                product_id=product.id if product.id is not None else item.product_id,
                transaction_id=transaction_id,
                qty_change=item.qty,
                unit_cost=item.unit_price,
            )
        )

    journal_entry = DomainJournalEntry(
        entry_date=entry_date,
        description="Pembelian barang dagang tunai",
        transaction_id=transaction_id,
        lines=[
            DomainJournalLine(
                account_code=AccountCode.INVENTORY.value,
                debit=total_purchase_amount,
                credit=Decimal("0.00"),
            ),
            DomainJournalLine(
                account_code=AccountCode.CASH.value,
                debit=Decimal("0.00"),
                credit=total_purchase_amount,
            ),
        ],
    )

    validate_journal_balance(journal_entry)

    return AccountingExecutionResult(
        updated_products=updated_products,
        stock_movements=stock_movements,
        journal_entry=journal_entry,
    )


def process_sale_transaction(
    products_by_id: dict[int, DomainProduct],
    items: list[DomainTransactionItem],
    transaction_id: int | None,
    entry_date: date,
) -> AccountingExecutionResult:
    """Process a confirmed sale transaction.

    Dua pasang jurnal dalam satu entry:
    1. Debit Kas (1-101), Kredit Penjualan (4-101) sebesar qty * unit_price (harga jual).
    2. Debit HPP (5-101), Kredit Persediaan (1-301) sebesar qty * avg_cost.
    """
    stock_movements: list[DomainStockMovement] = []
    updated_products: list[DomainProduct] = []
    total_sales_revenue = Decimal("0.00")
    total_cogs = Decimal("0.00")

    for item in items:
        product = products_by_id.get(item.product_id)
        if product is None:
            raise KeyError(f"Product ID {item.product_id} not found")

        # Cek ketersediaan stok
        if item.qty > product.stock_qty:
            msg = (
                f"Stok untuk '{product.name}' tidak mencukupi: "
                f"diminta {item.qty}, tersedia {product.stock_qty}."
            )
            raise InsufficientStockError(msg)

        # Jika harga jual kosong, gunakan harga jual default produk
        unit_price = (
            item.unit_price
            if item.unit_price is not None and item.unit_price > Decimal("0.00")
            else product.sell_price
        )

        sales_amount = round_currency(Decimal(item.qty) * unit_price)
        cogs_amount = round_currency(Decimal(item.qty) * product.avg_cost)

        total_sales_revenue += sales_amount
        total_cogs += cogs_amount

        # Update stok produk (avg_cost tetap saat penjualan)
        product.stock_qty -= item.qty
        updated_products.append(product)

        stock_movements.append(
            DomainStockMovement(
                product_id=product.id if product.id is not None else item.product_id,
                transaction_id=transaction_id,
                qty_change=-item.qty,
                unit_cost=product.avg_cost,
            )
        )

    journal_entry = DomainJournalEntry(
        entry_date=entry_date,
        description="Penjualan barang dagang tunai dan pencatatan HPP",
        transaction_id=transaction_id,
        lines=[
            DomainJournalLine(
                account_code=AccountCode.CASH.value,
                debit=total_sales_revenue,
                credit=Decimal("0.00"),
            ),
            DomainJournalLine(
                account_code=AccountCode.SALES_REVENUE.value,
                debit=Decimal("0.00"),
                credit=total_sales_revenue,
            ),
            DomainJournalLine(
                account_code=AccountCode.COGS.value,
                debit=total_cogs,
                credit=Decimal("0.00"),
            ),
            DomainJournalLine(
                account_code=AccountCode.INVENTORY.value,
                debit=Decimal("0.00"),
                credit=total_cogs,
            ),
        ],
    )

    validate_journal_balance(journal_entry)

    return AccountingExecutionResult(
        updated_products=updated_products,
        stock_movements=stock_movements,
        journal_entry=journal_entry,
    )


def execute_accounting_transaction(
    context: TransactionExecutionContext,
) -> AccountingExecutionResult | None:
    """Execute accounting logic for confirmed transactions.

    Returns None without altering stock or journals if transaction status is pending or rejected.
    """
    type_str = (
        context.tx_type.value
        if isinstance(context.tx_type, TransactionType)
        else context.tx_type
    )
    status_str = (
        context.tx_status.value
        if isinstance(context.tx_status, TransactionStatus)
        else context.tx_status
    )

    # Transaksi pending atau rejected tidak mengubah stok dan jurnal
    if status_str != TransactionStatus.CONFIRMED.value:
        return None

    if type_str == TransactionType.SALE.value:
        return process_sale_transaction(
            products_by_id=context.products_by_id,
            items=context.items,
            transaction_id=context.transaction_id,
            entry_date=context.entry_date,
        )

    if type_str == TransactionType.PURCHASE.value:
        return process_purchase_transaction(
            products_by_id=context.products_by_id,
            items=context.items,
            transaction_id=context.transaction_id,
            entry_date=context.entry_date,
        )

    if type_str == TransactionType.OPENING.value:
        products = list(context.products_by_id.values())
        return process_opening_transaction(
            products=products,
            transaction_id=context.transaction_id,
            entry_date=context.entry_date,
        )

    raise ValueError(f"Unsupported transaction type: {context.tx_type}")
