"""Unit and integration tests for slow-moving promo analysis and API endpoint."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.domain.models import DomainProduct, ProductMovementStats
from app.infrastructure.db.models import Base, Product, Transaction, TransactionItem
from app.infrastructure.db.repositories import PromoRepository
from app.main import app
from app.services.promo_service import (
    PromoLLMOutput,
    PromoService,
    SlowMovingPromoOptions,
    calculate_bundle_offer,
)

client = TestClient(app)
AUTH_HEADERS = {"X-API-Key": settings.api_secret}


def test_promo_endpoint_requires_api_key():
    """Verify POST /promo/slow-moving rejects missing or invalid X-API-Key."""
    res_no_key = client.post("/promo/slow-moving")
    assert res_no_key.status_code == 401

    res_bad_key = client.post(
        "/promo/slow-moving",
        headers={"X-API-Key": "wrong-secret"},
        json={"window_days": 30},
    )
    assert res_bad_key.status_code == 401


def test_calculate_bundle_offer_never_discounts_below_cogs():
    """Verify deterministic Decimal bundle math keeps price >= combined avg_cost."""
    slow = ProductMovementStats(
        product=DomainProduct(
            id=1,
            name="Senar Litecate",
            category="peralatan",
            unit="gulung",
            sell_price=Decimal("35000.00"),
            avg_cost=Decimal("29166.67"),
            stock_qty=10,
            min_stock=3,
        ),
        sold_qty_window=0,
        last_sold_date=None,
        days_since_last_sale=None,
        stock_value=Decimal("291666.70"),
    )
    partner = ProductMovementStats(
        product=DomainProduct(
            id=2,
            name="Pelet Jitu",
            category="bahan_umpan",
            unit="bungkus",
            sell_price=Decimal("3000.00"),
            avg_cost=Decimal("2500.00"),
            stock_qty=20,
            min_stock=5,
        ),
        sold_qty_window=8,
        last_sold_date=date(2026, 11, 14),
        days_since_last_sale=1,
        stock_value=Decimal("50000.00"),
    )

    bundle = calculate_bundle_offer(slow, partner)
    assert bundle.normal_total_price == Decimal("38000.00")
    assert bundle.discount_amount == Decimal("2000.00")
    assert bundle.bundle_price == Decimal("36000.00")
    assert bundle.bundle_price >= (slow.product.avg_cost + partner.product.avg_cost)

    # Kasus margin tipis: tidak boleh rugi di bawah total HPP
    thin_slow = ProductMovementStats(
        product=DomainProduct(
            id=3,
            name="Timah Tipis",
            category="peralatan",
            unit="bungkus",
            sell_price=Decimal("5000.00"),
            avg_cost=Decimal("4900.00"),
            stock_qty=5,
            min_stock=2,
        ),
        sold_qty_window=0,
        last_sold_date=None,
        days_since_last_sale=None,
        stock_value=Decimal("24500.00"),
    )
    thin_partner = ProductMovementStats(
        product=DomainProduct(
            id=4,
            name="Kail Kecil",
            category="peralatan",
            unit="bungkus",
            sell_price=Decimal("5000.00"),
            avg_cost=Decimal("4800.00"),
            stock_qty=5,
            min_stock=2,
        ),
        sold_qty_window=2,
        last_sold_date=date(2026, 11, 14),
        days_since_last_sale=1,
        stock_value=Decimal("24000.00"),
    )
    thin_bundle = calculate_bundle_offer(thin_slow, thin_partner)
    assert thin_bundle.discount_amount == Decimal("0.00")
    assert thin_bundle.bundle_price == Decimal("10000.00")


def test_promo_service_sqlite_movement_and_mocked_llm():
    """Verify PromoRepository SQLite velocity query + PromoService prompt and output."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    try:
        senar = Product(
            name="Senar Litecate",
            category="peralatan",
            unit="gulung",
            sell_price=Decimal("35000.00"),
            avg_cost=Decimal("29166.67"),
            stock_qty=10,
            min_stock=3,
        )
        pelet = Product(
            name="Pelet Jitu",
            category="bahan_umpan",
            unit="bungkus",
            sell_price=Decimal("3000.00"),
            avg_cost=Decimal("2500.00"),
            stock_qty=15,
            min_stock=5,
        )
        habis = Product(
            name="Kail Kosong",
            category="peralatan",
            unit="bungkus",
            sell_price=Decimal("10000.00"),
            avg_cost=Decimal("8000.00"),
            stock_qty=0,
            min_stock=2,
        )
        session.add_all([senar, pelet, habis])
        session.flush()

        # Penjualan confirmed hanya untuk Pelet Jitu; transaksi pending untuk Senar diabaikan
        tx_confirmed = Transaction(
            type="sale",
            status="confirmed",
            source="chat",
            raw_text="laku pelet jitu 4",
            confirmed_at=datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc),
        )
        tx_pending = Transaction(
            type="sale",
            status="pending",
            source="chat",
            raw_text="laku senar litecate 2",
        )
        session.add_all([tx_confirmed, tx_pending])
        session.flush()
        session.add_all(
            [
                TransactionItem(
                    transaction_id=tx_confirmed.id,
                    product_id=pelet.id,
                    qty=4,
                    unit_price=Decimal("3000.00"),
                ),
                TransactionItem(
                    transaction_id=tx_pending.id,
                    product_id=senar.id,
                    qty=2,
                    unit_price=Decimal("35000.00"),
                ),
            ]
        )
        session.commit()

        stats = PromoRepository(session).get_product_movement_stats(
            window_days=30,
            reference_date=date(2026, 11, 15),
        )
        # Produk stok 0 tidak ikut promo
        assert [s.product.name for s in stats] == ["Senar Litecate", "Pelet Jitu"]
        assert stats[0].sold_qty_window == 0
        assert stats[1].sold_qty_window == 4
        assert stats[1].days_since_last_sale == 1

        mock_llm = MagicMock()
        mock_llm.generate_structured.return_value = PromoLLMOutput(
            caption="Promo mancing! Paket Senar Litecate + Pelet Jitu cuma Rp36.000 (hemat Rp2.000 dari harga normal Rp38.000).",
            bundling_advice="Pasangkan Senar Litecate (stok 10 gulung) dengan Pelet Jitu yang laris di harga paket Rp36.000.",
        )

        result = PromoService(llm_client=mock_llm).generate_slow_moving_promo(
            options=SlowMovingPromoOptions(
                window_days=30,
                limit=3,
                reference_date=date(2026, 11, 15),
            ),
            session=session,
        )
        assert result["has_slow_moving"] is True
        # Hanya item nol penjualan yang dipilih sebagai slow moving saat masih ada zero mover
        assert len(result["slow_moving_items"]) == 1
        assert result["slow_moving_items"][0]["product_name"] == "Senar Litecate"
        assert result["bundle_suggestions"][0]["partner_product_name"] == "Pelet Jitu"
        assert result["bundle_suggestions"][0]["bundle_price_formatted"] == "Rp36.000"
        assert "Rp36.000" in result["summary_text"]

        # Pastikan prompt membawa angka data asli yang sudah dihitung Python
        llm_req = mock_llm.generate_structured.call_args.args[0]
        prompt_sent = llm_req.messages[0]["content"]
        assert llm_req.prompt_version == "promo_v1"
        assert "Rp35.000" in prompt_sent
        assert "Rp36.000" in prompt_sent
        assert "10 gulung" in prompt_sent
    finally:
        session.close()


def test_post_promo_slow_moving_endpoint_with_empty_body_and_empty_catalog():
    """Verify POST /promo/slow-moving works with empty body and handles no stock."""
    empty_repo = MagicMock()
    empty_repo.get_product_movement_stats.return_value = []
    mock_llm = MagicMock()

    service = PromoService(promo_repo=empty_repo, llm_client=mock_llm)
    with patch("app.api.promo.PromoService", return_value=service):
        res = client.post("/promo/slow-moving", headers=AUTH_HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert data["has_slow_moving"] is False
        assert data["slow_moving_items"] == []
        assert data["bundle_suggestions"] == []
        mock_llm.generate_structured.assert_not_called()
