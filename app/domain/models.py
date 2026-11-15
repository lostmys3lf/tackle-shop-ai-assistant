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


@dataclass
class SalesSummaryData:
    """Aggregated sales summary for a date range."""

    start_date: date
    end_date: date
    total_sales: Decimal
    transaction_count: int
    total_items_sold: int


@dataclass
class TopProductData:
    """Best-selling product statistics for a date range."""

    product_id: int
    product_name: str
    category: str
    unit: str
    total_qty: int
    total_revenue: Decimal


@dataclass
class ProfitLossData:
    """Monthly gross profit and loss statement calculated from journal entries."""

    month: str
    total_sales: Decimal
    total_cogs: Decimal
    gross_profit: Decimal


@dataclass
class ProductMovementStats:
    """Aggregated inventory and sales velocity stats for promo analysis."""

    product: DomainProduct
    sold_qty_window: int
    last_sold_date: date | None
    days_since_last_sale: int | None
    stock_value: Decimal


@dataclass
class BundleSuggestionData:
    """Deterministic bundling price breakdown for a slow-moving product pair."""

    slow_product: DomainProduct
    partner_product: DomainProduct
    normal_total_price: Decimal
    bundle_price: Decimal
    discount_amount: Decimal
    discount_percent: int
