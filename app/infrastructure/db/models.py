"""SQLAlchemy ORM models for the PostgreSQL database schema."""

# pylint: disable=too-few-public-methods

from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy ORM models."""


class Account(Base):
    """Chart of accounts entity."""

    __tablename__ = "accounts"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relasi ke journal lines
    journal_lines: Mapped[list["JournalLine"]] = relationship(back_populates="account")


class Product(Base):
    """Product inventory item."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    sell_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    avg_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    stock_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    min_stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relasi
    transaction_items: Mapped[list["TransactionItem"]] = relationship(
        back_populates="product"
    )
    stock_movements: Mapped[list["StockMovement"]] = relationship(
        back_populates="product"
    )


class Transaction(Base):
    """Business transaction record."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relasi
    items: Mapped[list["TransactionItem"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    stock_movements: Mapped[list["StockMovement"]] = relationship(
        back_populates="transaction"
    )
    journal_entries: Mapped[list["JournalEntry"]] = relationship(
        back_populates="transaction"
    )


class TransactionItem(Base):
    """Line item belonging to a transaction."""

    __tablename__ = "transaction_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id"), nullable=False
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relasi
    transaction: Mapped["Transaction"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship(back_populates="transaction_items")


class StockMovement(Base):
    """Record of inventory quantity changes."""

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id"), nullable=False
    )
    transaction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False
    )
    qty_change: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relasi
    product: Mapped["Product"] = relationship(back_populates="stock_movements")
    transaction: Mapped["Transaction"] = relationship(back_populates="stock_movements")


class JournalEntry(Base):
    """Header record for double-entry accounting journals."""

    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relasi
    transaction: Mapped["Transaction | None"] = relationship(
        back_populates="journal_entries"
    )
    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="journal_entry", cascade="all, delete-orphan"
    )


class JournalLine(Base):
    """Debit or credit line entry in an accounting journal."""

    __tablename__ = "journal_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    journal_entry_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("journal_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.code"), nullable=False
    )
    debit: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0.00"), nullable=False
    )
    credit: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=Decimal("0.00"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relasi
    journal_entry: Mapped["JournalEntry"] = relationship(back_populates="lines")
    account: Mapped["Account"] = relationship(back_populates="journal_lines")


class Message(Base):
    """Log record of incoming and outgoing chat messages."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intent_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    reply_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
