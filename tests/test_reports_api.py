"""Unit and integration tests for reports endpoints and ReportService."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.domain.models import (
    AccountCode,
    AccountType,
    DomainProduct,
    ProfitLossData,
    SalesSummaryData,
    TopProductData,
)
from app.infrastructure.db.models import (
    Account,
    Base,
    JournalEntry,
    JournalLine,
    Product,
    Transaction,
    TransactionItem,
)
from app.main import app
from app.services.report_service import ReportService

client = TestClient(app)
AUTH_HEADERS = {"X-API-Key": settings.api_secret}


def test_report_endpoints_require_api_key():
    """Verify all /reports endpoints enforce X-API-Key authentication."""
    assert client.get("/reports/low-stock").status_code == 401
    assert client.get("/reports/sales").status_code == 401
    assert client.get("/reports/profit-loss").status_code == 401


def test_get_low_stock_report_endpoint():
    """Verify GET /reports/low-stock returns low-stock items and alert summary."""
    low_items = [
        DomainProduct(
            id=1,
            name="Senar Litecate",
            category="peralatan",
            unit="gulung",
            sell_price=Decimal("35000.00"),
            avg_cost=Decimal("29000.00"),
            stock_qty=2,
            min_stock=3,
        )
    ]

    with patch("app.api.reports.ReportService") as mock_service_cls:
        mock_service_cls.return_value.get_low_stock_report.return_value = ReportService(
            report_repo=MagicMock(get_low_stock=MagicMock(return_value=low_items))
        ).get_low_stock_report()

        res = client.get("/reports/low-stock", headers=AUTH_HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert data["count"] == 1
        assert data["has_low_stock"] is True
        assert data["items"][0]["name"] == "Senar Litecate"
        assert "Senar Litecate: sisa 2 gulung" in data["summary_text"]


def test_get_sales_report_endpoint_and_validation():
    """Verify GET /reports/sales returns summary/top products and validates dates."""
    mock_repo = MagicMock()
    mock_repo.get_sales_summary.return_value = SalesSummaryData(
        start_date=date(2026, 11, 7),
        end_date=date(2026, 11, 13),
        total_sales=Decimal("76000.00"),
        transaction_count=3,
        total_items_sold=5,
    )
    mock_repo.get_top_products.return_value = [
        TopProductData(
            product_id=1,
            product_name="Senar Litecate",
            category="peralatan",
            unit="gulung",
            total_qty=2,
            total_revenue=Decimal("70000.00"),
        )
    ]

    with patch("app.api.reports.ReportService") as mock_service_cls:
        mock_service_cls.return_value.get_sales_report.return_value = ReportService(
            report_repo=mock_repo
        ).get_sales_report(start=date(2026, 11, 7), end=date(2026, 11, 13))

        res = client.get(
            "/reports/sales?start=2026-11-07&end=2026-11-13",
            headers=AUTH_HEADERS,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total_sales_formatted"] == "Rp76.000"
        assert data["transaction_count"] == 3
        assert data["top_products"][0]["product_name"] == "Senar Litecate"
        assert "Rp76.000" in data["summary_text"]

    # Invalid range end < start must return HTTP 400
    bad_res = client.get(
        "/reports/sales?start=2026-11-13&end=2026-11-01",
        headers=AUTH_HEADERS,
    )
    assert bad_res.status_code == 400


def test_get_profit_loss_endpoint_and_validation():
    """Verify GET /reports/profit-loss returns monthly P&L and rejects bad month."""
    mock_repo = MagicMock()
    mock_repo.get_profit_loss.return_value = ProfitLossData(
        month="2026-11",
        total_sales=Decimal("76000.00"),
        total_cogs=Decimal("60000.00"),
        gross_profit=Decimal("16000.00"),
    )

    with patch("app.api.reports.ReportService") as mock_service_cls:
        mock_service_cls.return_value.get_profit_loss_report.return_value = (
            ReportService(report_repo=mock_repo).get_profit_loss_report(month="2026-11")
        )

        res = client.get("/reports/profit-loss?month=2026-11", headers=AUTH_HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert data["month"] == "2026-11"
        assert data["gross_profit_formatted"] == "Rp16.000"
        assert "Laba Kotor: Rp16.000" in data["summary_text"]

    with patch("app.infrastructure.db.connection.get_session") as mock_session_ctx:
        mock_session_ctx.return_value.__enter__.return_value = MagicMock()
        bad_res = client.get(
            "/reports/profit-loss?month=2026-13",
            headers=AUTH_HEADERS,
        )
        assert bad_res.status_code == 400


def test_report_service_integration_with_sqlite():
    """Verify ReportService computes accurate values against SQLite ORM data."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    try:
        session.add_all(
            [
                Account(
                    code=AccountCode.SALES_REVENUE.value,
                    name="Penjualan",
                    type=AccountType.REVENUE.value,
                ),
                Account(
                    code=AccountCode.COGS.value,
                    name="HPP",
                    type=AccountType.EXPENSE.value,
                ),
            ]
        )
        p1 = Product(
            name="Pelet Jitu",
            category="bahan_umpan",
            unit="bungkus",
            sell_price=Decimal("3000.00"),
            avg_cost=Decimal("2500.00"),
            stock_qty=3,
            min_stock=5,
        )
        p2 = Product(
            name="Senar Shino",
            category="peralatan",
            unit="gulung",
            sell_price=Decimal("35000.00"),
            avg_cost=Decimal("29000.00"),
            stock_qty=10,
            min_stock=3,
        )
        session.add_all([p1, p2])
        session.flush()

        tx = Transaction(
            type="sale",
            status="confirmed",
            source="chat",
            raw_text="laku pelet jitu 2",
            confirmed_at=datetime(2026, 11, 13, 8, 0, tzinfo=timezone.utc),
        )
        session.add(tx)
        session.flush()
        session.add(
            TransactionItem(
                transaction_id=tx.id,
                product_id=p1.id,
                qty=2,
                unit_price=Decimal("3000.00"),
            )
        )
        je = JournalEntry(
            transaction_id=tx.id,
            entry_date=date(2026, 11, 13),
            description="Penjualan",
        )
        session.add(je)
        session.flush()
        session.add_all(
            [
                JournalLine(
                    journal_entry_id=je.id,
                    account_code=AccountCode.SALES_REVENUE.value,
                    debit=Decimal("0.00"),
                    credit=Decimal("6000.00"),
                ),
                JournalLine(
                    journal_entry_id=je.id,
                    account_code=AccountCode.COGS.value,
                    debit=Decimal("5000.00"),
                    credit=Decimal("0.00"),
                ),
            ]
        )
        session.commit()

        svc = ReportService()
        low_stock = svc.get_low_stock_report(session=session)
        assert low_stock["count"] == 1
        assert low_stock["items"][0]["name"] == "Pelet Jitu"

        sales = svc.get_sales_report(
            start=date(2026, 11, 13),
            end=date(2026, 11, 13),
            session=session,
        )
        assert sales["total_sales"] == Decimal("6000.00")
        assert sales["top_products"][0]["total_qty"] == 2

        pnl = svc.get_profit_loss_report(month="2026-11", session=session)
        assert pnl["gross_profit"] == Decimal("1000.00")
    finally:
        session.close()
