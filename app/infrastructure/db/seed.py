"""Database seeding script for chart of accounts and initial products from CSV."""

import csv
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.accounting.rules import process_opening_transaction
from app.domain.models import AccountType, DomainProduct
from app.infrastructure.db.connection import get_session
from app.infrastructure.db.models import (
    Account,
    JournalEntry,
    JournalLine,
    Product,
    StockMovement,
    Transaction,
    TransactionItem,
)

logger = logging.getLogger(__name__)

INITIAL_ACCOUNTS = [
    {"code": "1-101", "name": "Kas", "type": AccountType.ASSET.value},
    {
        "code": "1-301",
        "name": "Persediaan Barang Dagang",
        "type": AccountType.ASSET.value,
    },
    {"code": "3-101", "name": "Modal Pemilik", "type": AccountType.EQUITY.value},
    {"code": "4-101", "name": "Penjualan", "type": AccountType.REVENUE.value},
    {
        "code": "5-101",
        "name": "Harga Pokok Penjualan",
        "type": AccountType.EXPENSE.value,
    },
]

DEFAULT_CSV_PATH = Path("db/seed/products.csv")


class EmptySeedFileError(Exception):
    """Raised when the products CSV seed file is missing or has no product rows."""


def seed_accounts(session: Session) -> int:
    """Seed the initial chart of accounts if not already present."""
    inserted_count = 0
    for acc in INITIAL_ACCOUNTS:
        existing = session.scalar(select(Account).where(Account.code == acc["code"]))
        if existing is None:
            session.add(Account(code=acc["code"], name=acc["name"], type=acc["type"]))
            inserted_count += 1
    return inserted_count


def load_products_from_csv(csv_path: str | Path) -> list[dict]:
    """Parse products from CSV file and validate that records are present."""
    path = Path(csv_path)
    if not path.is_file():
        raise EmptySeedFileError(f"Seed file '{path}' does not exist.")

    if path.stat().st_size == 0:
        raise EmptySeedFileError(f"Seed file '{path}' is completely empty (0 bytes).")

    products: list[dict] = []
    with open(path, mode="r", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise EmptySeedFileError(
                f"Seed file '{path}' is empty or has no header columns."
            )

        for row_index, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            if not name:
                continue

            try:
                products.append(
                    {
                        "name": name,
                        "category": (row.get("category") or "peralatan").strip(),
                        "unit": (row.get("unit") or "buah").strip(),
                        "purchase_price": Decimal(
                            str(row.get("purchase_price") or "0")
                        ),
                        "sell_price": Decimal(str(row.get("sell_price") or "0")),
                        "stock_qty": int(str(row.get("stock_qty") or "0")),
                        "min_stock": int(str(row.get("min_stock") or "0")),
                    }
                )
            except (ValueError, TypeError) as err:
                raise ValueError(
                    f"Invalid data format in '{path}' at row {row_index}: {err}"
                ) from err

    if not products:
        raise EmptySeedFileError(
            f"Seed file '{path}' contains header but no data rows."
        )

    return products


def _insert_seed_products(
    session: Session, product_records: list[dict]
) -> tuple[list[Product], list[DomainProduct]]:
    """Insert product rows into database and return domain product mappings."""
    db_products: list[Product] = []
    domain_products: list[DomainProduct] = []

    for record in product_records:
        product = Product(
            name=record["name"],
            category=record["category"],
            unit=record["unit"],
            sell_price=record["sell_price"],
            avg_cost=record["purchase_price"],  # avg_cost awal = harga beli
            stock_qty=record["stock_qty"],
            min_stock=record["min_stock"],
        )
        session.add(product)
        db_products.append(product)

    session.flush()

    for p in db_products:
        domain_products.append(
            DomainProduct(
                id=p.id,
                name=p.name,
                category=p.category,
                unit=p.unit,
                sell_price=p.sell_price,
                avg_cost=p.avg_cost,
                stock_qty=p.stock_qty,
                min_stock=p.min_stock,
            )
        )

    return db_products, domain_products


def _record_opening_journal_and_movements(
    session: Session, tx_id: int, accounting_result
) -> None:
    """Persist stock movements and journal entry for opening inventory."""
    for sm in accounting_result.stock_movements:
        session.add(
            StockMovement(
                product_id=sm.product_id,
                transaction_id=tx_id,
                qty_change=sm.qty_change,
                unit_cost=sm.unit_cost,
            )
        )

    domain_entry = accounting_result.journal_entry
    journal_entry = JournalEntry(
        transaction_id=tx_id,
        entry_date=domain_entry.entry_date,
        description=domain_entry.description,
    )
    session.add(journal_entry)
    session.flush()

    for line in domain_entry.lines:
        session.add(
            JournalLine(
                journal_entry_id=journal_entry.id,
                account_code=line.account_code,
                debit=line.debit,
                credit=line.credit,
            )
        )


def seed_products_and_opening(
    session: Session, csv_path: str | Path = DEFAULT_CSV_PATH
) -> bool:
    """Seed initial products, create opening transaction, stock movements, and initial journal."""
    existing_product = session.scalar(select(Product.id).limit(1))
    if existing_product is not None:
        logger.info("Products table already has data. Skipping opening seed.")
        return False

    product_records = load_products_from_csv(csv_path)
    db_products, domain_products = _insert_seed_products(session, product_records)

    now = datetime.now(timezone.utc)
    opening_tx = Transaction(
        type="opening",
        status="confirmed",
        source="seed",
        raw_text=f"Saldo awal produk dari seed CSV ({Path(csv_path).name})",
        confirmed_at=now,
    )
    session.add(opening_tx)
    session.flush()

    for p in db_products:
        session.add(
            TransactionItem(
                transaction_id=opening_tx.id,
                product_id=p.id,
                qty=p.stock_qty,
                unit_price=p.avg_cost,
            )
        )

    accounting_result = process_opening_transaction(
        products=domain_products,
        transaction_id=opening_tx.id,
        entry_date=date.today(),
    )
    _record_opening_journal_and_movements(session, opening_tx.id, accounting_result)

    return True


def run_seed(csv_path: str | Path = DEFAULT_CSV_PATH) -> None:
    """Run full seeding sequence wrapped in a database session."""
    with get_session() as session:
        accounts_seeded = seed_accounts(session)
        logger.info("Accounts seeded: %d", accounts_seeded)
        products_seeded = seed_products_and_opening(session, csv_path=csv_path)
        logger.info("Products seeded: %s", products_seeded)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        run_seed()
        print("Database seed completed successfully.")
    except EmptySeedFileError as error:
        print(f"Error seeding database: {error}")
