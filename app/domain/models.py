"""Domain models for products, transactions, stock, and double-entry journals."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class AccountCode(str, Enum):
    """Chart of accounts standard codes."""

    CASH = "1-101"
    INVENTORY = "1-301"
    OWNER_EQUITY = "3-101"
    SALES_REVENUE = "4-101"
    COGS = "5-101"


class AccountType(str, Enum):
    """Account classification types."""

    ASSET = "asset"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


class TransactionType(str, Enum):
    """Business transaction types."""

    SALE = "sale"
    PURCHASE = "purchase"
    OPENING = "opening"


class TransactionStatus(str, Enum):
    """Lifecycle status of a transaction."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class TransactionSource(str, Enum):
    """Input source of a transaction."""

    CHAT = "chat"
    PHOTO = "photo"
    SEED = "seed"


@dataclass
class DomainProduct:  # pylint: disable=too-many-instance-attributes
    """Product domain entity."""

    id: int | None
    name: str
    category: str
    unit: str
    sell_price: Decimal
    avg_cost: Decimal
    stock_qty: int
    min_stock: int
    created_at: datetime | None = None


@dataclass
class DomainTransactionItem:
    """Individual item inside a transaction."""

    product_id: int
    qty: int
    unit_price: Decimal
    product_name_raw: str | None = None


@dataclass
class DomainStockMovement:
    """Inventory quantity adjustment record."""

    product_id: int
    qty_change: int
    unit_cost: Decimal
    transaction_id: int | None = None


@dataclass
class DomainJournalLine:
    """Individual debit or credit line in a journal entry."""

    account_code: str
    debit: Decimal = Decimal("0.00")
    credit: Decimal = Decimal("0.00")


@dataclass
class DomainJournalEntry:
    """Double-entry accounting journal header and lines."""

    entry_date: date
    description: str
    transaction_id: int | None = None
    lines: list[DomainJournalLine] = field(default_factory=list)


@dataclass
class TransactionExecutionContext:
    """Context parameters required to execute accounting transaction processing."""

    tx_type: str | TransactionType
    tx_status: str | TransactionStatus
    products_by_id: dict[int, DomainProduct]
    items: list[DomainTransactionItem]
    transaction_id: int | None
    entry_date: date


@dataclass
class AccountingExecutionResult:
    """Result of processing a confirmed transaction in the accounting domain."""

    updated_products: list[DomainProduct]
    stock_movements: list[DomainStockMovement]
    journal_entry: DomainJournalEntry
