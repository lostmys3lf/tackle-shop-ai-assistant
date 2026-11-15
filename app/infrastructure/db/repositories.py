"""Database repositories isolating SQLAlchemy queries from service layer."""

# pylint: disable=too-few-public-methods

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from app.domain.models import (
    AccountCode,
    AccountingExecutionResult,
    DomainProduct,
    ProductMovementStats,
    ProfitLossData,
    SalesSummaryData,
    TopProductData,
    TransactionStatus,
    TransactionType,
)
from app.infrastructure.db.models import (
    JournalEntry,
    JournalLine,
    Message,
    Product,
    StockMovement,
    Transaction,
    TransactionItem,
)


def _to_domain_product(orm: Product) -> DomainProduct:
    """Convert Product ORM model to DomainProduct entity."""
    return DomainProduct(
        id=orm.id,
        name=orm.name,
        category=orm.category,
        unit=orm.unit,
        sell_price=orm.sell_price,
        avg_cost=orm.avg_cost,
        stock_qty=orm.stock_qty,
        min_stock=orm.min_stock,
        created_at=orm.created_at,
    )


class ProductRepository:
    """Repository for managing product inventory data."""

    def __init__(self, session: Session) -> None:
        """Initialize repository with database session."""
        self.session = session

    def get_all(self, category: str | None = None) -> list[DomainProduct]:
        """Retrieve all registered products, optionally filtered by category."""
        stmt = select(Product)
        if category is not None:
            stmt = stmt.where(Product.category == category)
        stmt = stmt.order_by(Product.id)
        rows = self.session.scalars(stmt).all()
        return [_to_domain_product(row) for row in rows]

    def get_by_id(self, product_id: int) -> DomainProduct | None:
        """Retrieve a single product by its integer ID."""
        orm = self.session.get(Product, product_id)
        if orm is None:
            return None
        return _to_domain_product(orm)

    def get_by_ids(self, product_ids: list[int]) -> dict[int, DomainProduct]:
        """Retrieve multiple products mapped by their ID."""
        if not product_ids:
            return {}
        stmt = select(Product).where(Product.id.in_(product_ids))
        rows = self.session.scalars(stmt).all()
        return {row.id: _to_domain_product(row) for row in rows}


class TransactionRepository:
    """Repository for managing transactions, movements, and double-entry journals."""

    def __init__(self, session: Session) -> None:
        """Initialize repository with database session."""
        self.session = session

    def create_pending_transaction(
        self,
        tx_type: str,
        source: str,
        raw_text: str | None,
        items: list[dict[str, Any]],
    ) -> Transaction:
        """Create a new transaction in pending status with its line items."""
        tx = Transaction(
            type=tx_type,
            status=TransactionStatus.PENDING.value,
            source=source,
            raw_text=raw_text,
        )
        self.session.add(tx)
        self.session.flush()

        for item in items:
            tx_item = TransactionItem(
                transaction_id=tx.id,
                product_id=item["product_id"],
                qty=item["qty"],
                unit_price=item["unit_price"],
            )
            self.session.add(tx_item)

        self.session.flush()
        return tx

    def get_by_id(self, tx_id: int, for_update: bool = False) -> Transaction | None:
        """Retrieve transaction by ID, optionally locking the row for update."""
        stmt = select(Transaction).where(Transaction.id == tx_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.session.scalars(stmt).first()

    def reject_transaction(self, tx_id: int) -> Transaction | None:
        """Update transaction status to rejected."""
        tx = self.get_by_id(tx_id)
        if tx is None:
            return None
        tx.status = TransactionStatus.REJECTED.value
        self.session.flush()
        return tx

    def save_confirmed_transaction(
        self,
        tx_id: int,
        result: AccountingExecutionResult,
    ) -> None:
        """Apply accounting execution: update product stock/cost, movements, and journal."""
        tx = self.get_by_id(tx_id)
        if tx is None:
            raise KeyError(f"Transaction ID {tx_id} not found")

        # 1. Update product stock and weighted average cost
        for updated in result.updated_products:
            if updated.id is not None:
                p_orm = self.session.get(Product, updated.id)
                if p_orm:
                    p_orm.stock_qty = updated.stock_qty
                    p_orm.avg_cost = updated.avg_cost

        # 2. Record stock movements
        for mv in result.stock_movements:
            movement_orm = StockMovement(
                product_id=mv.product_id,
                transaction_id=tx.id,
                qty_change=mv.qty_change,
                unit_cost=mv.unit_cost,
            )
            self.session.add(movement_orm)

        # 3. Record journal entry and lines
        je = result.journal_entry
        journal_orm = JournalEntry(
            transaction_id=tx.id,
            entry_date=je.entry_date,
            description=je.description,
        )
        self.session.add(journal_orm)
        self.session.flush()

        for line in je.lines:
            line_orm = JournalLine(
                journal_entry_id=journal_orm.id,
                account_code=line.account_code,
                debit=line.debit,
                credit=line.credit,
            )
            self.session.add(line_orm)

        # 4. Update transaction status
        tx.status = TransactionStatus.CONFIRMED.value
        tx.confirmed_at = datetime.now(timezone.utc)
        self.session.flush()


class MessageRepository:
    """Repository for incoming chat and reply logging."""

    def __init__(self, session: Session) -> None:
        """Initialize repository with database session."""
        self.session = session

    def create_message(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        channel: str,
        chat_id: str,
        text: str | None,
        intent: str | None = None,
        confidence: Decimal | None = None,
        reply_text: str | None = None,
    ) -> Message:
        """Create and store a message log."""
        msg = Message(
            channel=channel,
            chat_id=chat_id,
            text=text,
            intent=intent,
            intent_confidence=confidence,
            reply_text=reply_text,
        )
        self.session.add(msg)
        self.session.flush()
        return msg

    def update_reply(
        self,
        message_id: int,
        reply_text: str,
        intent: str | None = None,
        confidence: Decimal | None = None,
    ) -> None:
        """Update existing message record with intent and reply text."""
        msg = self.session.get(Message, message_id)
        if msg:
            msg.reply_text = reply_text
            if intent is not None:
                msg.intent = intent
            if confidence is not None:
                msg.intent_confidence = confidence
            self.session.flush()


def _get_jakarta_tz() -> tzinfo:
    """Return Asia/Jakarta timezone with fixed UTC+7 fallback."""
    try:
        return ZoneInfo("Asia/Jakarta")
    except ZoneInfoNotFoundError:  # pragma: no cover
        return timezone(timedelta(hours=7))


JAKARTA_TZ: tzinfo = _get_jakarta_tz()


def get_jakarta_today() -> date:
    """Return current calendar date in Asia/Jakarta (WIB)."""
    return datetime.now(JAKARTA_TZ).date()


def _to_jakarta_date(value: datetime | str | None) -> date | None:
    """Normalize a database timestamp or ISO string to an Asia/Jakarta calendar date."""
    if value is None:
        return None
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JAKARTA_TZ).date()


def _build_movement_stat(
    product_orm: Product,
    sold_qty: int,
    last_sold_date: date | None,
    end_date: date,
) -> ProductMovementStats:
    """Assemble ProductMovementStats entity from ORM row and aggregated sale dates."""
    domain_product = _to_domain_product(product_orm)
    days_idle = (
        max(0, (end_date - last_sold_date).days) if last_sold_date is not None else None
    )
    stock_value = (
        Decimal(domain_product.stock_qty) * domain_product.avg_cost
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return ProductMovementStats(
        product=domain_product,
        sold_qty_window=sold_qty,
        last_sold_date=last_sold_date,
        days_since_last_sale=days_idle,
        stock_value=stock_value,
    )


class PromoRepository:
    """Repository querying product velocity and idle inventory data for promo suggestions."""

    def __init__(self, session: Session) -> None:
        """Initialize repository with database session."""
        self.session = session

    def _query_sold_quantities(
        self,
        start_utc: datetime,
        end_utc: datetime,
    ) -> dict[int, int]:
        """Return confirmed sold quantity per product in UTC timestamp bounds."""
        stmt = (
            select(
                TransactionItem.product_id,
                func.coalesce(func.sum(TransactionItem.qty), 0).label("sold_qty"),
            )
            .join(Transaction, TransactionItem.transaction_id == Transaction.id)
            .where(
                and_(
                    Transaction.type == TransactionType.SALE.value,
                    Transaction.status == TransactionStatus.CONFIRMED.value,
                    Transaction.confirmed_at.is_not(None),
                    Transaction.confirmed_at >= start_utc,
                    Transaction.confirmed_at < end_utc,
                )
            )
            .group_by(TransactionItem.product_id)
        )
        return {
            row.product_id: int(row.sold_qty)
            for row in self.session.execute(stmt).all()
        }

    def _query_last_sale_dates(self) -> dict[int, date | None]:
        """Return latest confirmed sale calendar date (WIB) per product."""
        stmt = (
            select(
                TransactionItem.product_id,
                func.max(Transaction.confirmed_at).label("last_confirmed_at"),
            )
            .join(Transaction, TransactionItem.transaction_id == Transaction.id)
            .where(
                and_(
                    Transaction.type == TransactionType.SALE.value,
                    Transaction.status == TransactionStatus.CONFIRMED.value,
                    Transaction.confirmed_at.is_not(None),
                )
            )
            .group_by(TransactionItem.product_id)
        )
        return {
            row.product_id: _to_jakarta_date(row.last_confirmed_at)
            for row in self.session.execute(stmt).all()
        }

    def get_product_movement_stats(
        self,
        window_days: int = 30,
        reference_date: date | None = None,
    ) -> list[ProductMovementStats]:
        """Compute active product sales velocity and idle duration in Asia/Jakarta time."""
        end_date = reference_date or get_jakarta_today()
        start_utc, end_utc = _jakarta_date_range_to_utc(
            end_date - timedelta(days=max(1, window_days) - 1),
            end_date,
        )

        products = self.session.scalars(
            select(Product).where(Product.stock_qty > 0).order_by(Product.id)
        ).all()
        if not products:
            return []

        sold_by_product = self._query_sold_quantities(start_utc, end_utc)
        last_sale_by_product = self._query_last_sale_dates()

        stats = [
            _build_movement_stat(
                product_orm=product_orm,
                sold_qty=sold_by_product.get(product_orm.id, 0),
                last_sold_date=last_sale_by_product.get(product_orm.id),
                end_date=end_date,
            )
            for product_orm in products
        ]

        # Urutkan dari penjualan terendah, paling lama tidak laku, lalu nilai stok terbesar
        stats.sort(
            key=lambda item: (
                item.sold_qty_window,
                -(
                    item.days_since_last_sale
                    if item.days_since_last_sale is not None
                    else 10**9
                ),
                -item.stock_value,
                item.product.id or 0,
            )
        )
        return stats


def _jakarta_date_range_to_utc(
    start_date: date, end_date: date
) -> tuple[datetime, datetime]:
    """Convert inclusive Asia/Jakarta dates to [start_utc, end_exclusive_utc) bounds."""
    if end_date < start_date:
        raise ValueError("Tanggal akhir tidak boleh lebih awal dari tanggal mulai.")
    start_local = datetime.combine(start_date, time.min, tzinfo=JAKARTA_TZ)
    end_local = datetime.combine(
        end_date + timedelta(days=1), time.min, tzinfo=JAKARTA_TZ
    )
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _parse_month_bounds(month: str) -> tuple[str, date, date]:
    """Parse YYYY-MM string into normalized month label and inclusive start/end dates."""
    clean = month.strip()
    parts = clean.split("-")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError("Format bulan harus YYYY-MM (contoh: 2026-11).")

    year = int(parts[0])
    month_num = int(parts[1])
    if month_num < 1 or month_num > 12:
        raise ValueError("Bulan harus bernilai antara 01 sampai 12.")

    month_start = date(year, month_num, 1)
    if month_num == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_num + 1, 1)
    month_end = next_month - timedelta(days=1)
    return f"{year:04d}-{month_num:02d}", month_start, month_end


def _to_money_decimal(value: Any) -> Decimal:
    """Convert SQL aggregate numeric value into 2-decimal Decimal safely."""
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))


class ReportRepository:
    """Read-only repository for inventory, sales summaries, top products, and P&L."""

    def __init__(self, session: Session) -> None:
        """Initialize report repository with database session."""
        self.session = session

    def get_stock(self, product_query: str | None = None) -> list[DomainProduct]:
        """Return active products matching query string, or all products if empty."""
        clean_query = (product_query or "").strip()
        if not clean_query:
            stmt = select(Product).order_by(Product.id)
            return [_to_domain_product(row) for row in self.session.scalars(stmt).all()]

        pattern = f"%{clean_query}%"
        stmt = (
            select(Product)
            .where(or_(Product.name.ilike(pattern), Product.category.ilike(pattern)))
            .order_by(Product.id)
        )
        rows = self.session.scalars(stmt).all()

        # Fallback pecah per kata jika frasa multi-kata tidak cocok persis berurutan
        tokens = [tok for tok in clean_query.split() if tok]
        if not rows and len(tokens) > 1:
            token_stmt = (
                select(Product)
                .where(and_(*(Product.name.ilike(f"%{tok}%") for tok in tokens)))
                .order_by(Product.id)
            )
            rows = self.session.scalars(token_stmt).all()

        return [_to_domain_product(row) for row in rows]

    def get_low_stock(self) -> list[DomainProduct]:
        """Return products with stock_qty at or below min_stock."""
        stmt = (
            select(Product)
            .where(Product.stock_qty <= Product.min_stock)
            .order_by(Product.stock_qty, Product.id)
        )
        rows = self.session.scalars(stmt).all()
        return [_to_domain_product(row) for row in rows]

    def get_sales_summary(self, start_date: date, end_date: date) -> SalesSummaryData:
        """Calculate confirmed sales total, transaction count, and items sold for date range."""
        start_utc, end_exclusive_utc = _jakarta_date_range_to_utc(start_date, end_date)
        stmt = (
            select(
                func.coalesce(
                    func.sum(TransactionItem.qty * TransactionItem.unit_price), 0
                ).label("total_sales"),
                func.count(func.distinct(Transaction.id)).label("tx_count"),
                func.coalesce(func.sum(TransactionItem.qty), 0).label("total_items"),
            )
            .select_from(Transaction)
            .outerjoin(
                TransactionItem, TransactionItem.transaction_id == Transaction.id
            )
            .where(
                Transaction.type == TransactionType.SALE.value,
                Transaction.status == TransactionStatus.CONFIRMED.value,
                Transaction.confirmed_at.is_not(None),
                Transaction.confirmed_at >= start_utc,
                Transaction.confirmed_at < end_exclusive_utc,
            )
        )
        row = self.session.execute(stmt).one()
        return SalesSummaryData(
            start_date=start_date,
            end_date=end_date,
            total_sales=_to_money_decimal(row.total_sales),
            transaction_count=int(row.tx_count or 0),
            total_items_sold=int(row.total_items or 0),
        )

    def get_top_products(
        self,
        start_date: date,
        end_date: date,
        limit: int = 5,
    ) -> list[TopProductData]:
        """Return best-selling products ordered by sold quantity within date range."""
        start_utc, end_exclusive_utc = _jakarta_date_range_to_utc(start_date, end_date)
        safe_limit = max(1, min(limit, 50))
        qty_sum = func.coalesce(func.sum(TransactionItem.qty), 0).label("total_qty")
        rev_sum = func.coalesce(
            func.sum(TransactionItem.qty * TransactionItem.unit_price), 0
        ).label("total_revenue")

        stmt = (
            select(
                Product.id.label("product_id"),
                Product.name.label("product_name"),
                Product.category.label("category"),
                Product.unit.label("unit"),
                qty_sum,
                rev_sum,
            )
            .select_from(TransactionItem)
            .join(Transaction, TransactionItem.transaction_id == Transaction.id)
            .join(Product, TransactionItem.product_id == Product.id)
            .where(
                Transaction.type == TransactionType.SALE.value,
                Transaction.status == TransactionStatus.CONFIRMED.value,
                Transaction.confirmed_at.is_not(None),
                Transaction.confirmed_at >= start_utc,
                Transaction.confirmed_at < end_exclusive_utc,
            )
            .group_by(Product.id, Product.name, Product.category, Product.unit)
            .order_by(desc(qty_sum), desc(rev_sum), Product.id)
            .limit(safe_limit)
        )
        rows = self.session.execute(stmt).all()
        return [
            TopProductData(
                product_id=row.product_id,
                product_name=row.product_name,
                category=row.category,
                unit=row.unit,
                total_qty=int(row.total_qty or 0),
                total_revenue=_to_money_decimal(row.total_revenue),
            )
            for row in rows
        ]

    def get_profit_loss(self, month: str) -> ProfitLossData:
        """Compute monthly sales revenue, COGS, and gross profit from journal lines."""
        normalized_month, month_start, month_end = _parse_month_bounds(month)
        stmt = (
            select(
                JournalLine.account_code.label("account_code"),
                func.coalesce(func.sum(JournalLine.debit), 0).label("total_debit"),
                func.coalesce(func.sum(JournalLine.credit), 0).label("total_credit"),
            )
            .select_from(JournalLine)
            .join(JournalEntry, JournalLine.journal_entry_id == JournalEntry.id)
            .where(
                JournalEntry.entry_date >= month_start,
                JournalEntry.entry_date <= month_end,
                JournalLine.account_code.in_(
                    [AccountCode.SALES_REVENUE.value, AccountCode.COGS.value]
                ),
            )
            .group_by(JournalLine.account_code)
        )
        rows = self.session.execute(stmt).all()

        total_sales = Decimal("0.00")
        total_cogs = Decimal("0.00")
        for row in rows:
            debit = _to_money_decimal(row.total_debit)
            credit = _to_money_decimal(row.total_credit)
            if row.account_code == AccountCode.SALES_REVENUE.value:
                total_sales = (credit - debit).quantize(Decimal("0.01"))
            elif row.account_code == AccountCode.COGS.value:
                total_cogs = (debit - credit).quantize(Decimal("0.01"))

        gross_profit = (total_sales - total_cogs).quantize(Decimal("0.01"))
        return ProfitLossData(
            month=normalized_month,
            total_sales=total_sales,
            total_cogs=total_cogs,
            gross_profit=gross_profit,
        )
