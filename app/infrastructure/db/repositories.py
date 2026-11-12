"""Database repositories isolating SQLAlchemy queries from service layer."""

# pylint: disable=too-few-public-methods

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    AccountingExecutionResult,
    DomainProduct,
    TransactionStatus,
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

    def get_all(self) -> list[DomainProduct]:
        """Retrieve all registered products."""
        stmt = select(Product).order_by(Product.id)
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

    def get_by_id(self, tx_id: int) -> Transaction | None:
        """Retrieve transaction by ID including loaded items."""
        stmt = select(Transaction).where(Transaction.id == tx_id)
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
