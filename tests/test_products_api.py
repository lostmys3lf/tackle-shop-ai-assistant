"""Unit and integration tests for GET /products endpoint and ProductService."""

from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.infrastructure.db.models import Base, Product
from app.main import app

client = TestClient(app)
AUTH_HEADERS = {"X-API-Key": settings.api_secret}


def test_products_endpoint_requires_api_key():
    """Verify GET /products enforces X-API-Key authentication."""
    assert client.get("/products").status_code == 401
    assert client.get("/products?category=peralatan").status_code == 401


def test_products_endpoint_filters_and_formats_from_sqlite():
    """Verify GET /products queries SQLite, filters category, and formats Rupiah text."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_local() as db_session:
        db_session.add_all(
            [
                Product(
                    id=1,
                    name="Senar Litecate",
                    category="peralatan",
                    unit="gulung",
                    sell_price=Decimal("35000.00"),
                    avg_cost=Decimal("29000.00"),
                    stock_qty=2,
                    min_stock=3,
                ),
                Product(
                    id=2,
                    name="Pelet Jitu",
                    category="bahan_umpan",
                    unit="bungkus",
                    sell_price=Decimal("3000.00"),
                    avg_cost=Decimal("2500.00"),
                    stock_qty=18,
                    min_stock=5,
                ),
            ]
        )
        db_session.commit()

    def _override_session():
        return session_local()

    with patch(
        "app.services.product_service.get_session", side_effect=_override_session
    ):
        res_all = client.get("/products", headers=AUTH_HEADERS)
        assert res_all.status_code == 200
        data_all = res_all.json()
        assert data_all["count"] == 2
        assert data_all["category"] is None
        assert "Daftar Semua Produk Toko:" in data_all["summary_text"]
        assert (
            "Senar Litecate: stok 2 gulung (menipis) | Harga Rp35.000"
            in data_all["summary_text"]
        )
        assert "Pelet Jitu: stok 18 bungkus | Harga Rp3.000" in data_all["summary_text"]

        res_gear = client.get("/products?category=peralatan", headers=AUTH_HEADERS)
        assert res_gear.status_code == 200
        data_gear = res_gear.json()
        assert data_gear["count"] == 1
        assert data_gear["category"] == "peralatan"
        assert data_gear["items"][0]["name"] == "Senar Litecate"
        assert "Daftar Produk (Peralatan):" in data_gear["summary_text"]
        assert "Pelet Jitu" not in data_gear["summary_text"]

        res_bait = client.get("/products?category=bahan_umpan", headers=AUTH_HEADERS)
        assert res_bait.status_code == 200
        data_bait = res_bait.json()
        assert data_bait["count"] == 1
        assert data_bait["category"] == "bahan_umpan"
        assert data_bait["items"][0]["sell_price_formatted"] == "Rp3.000"
        assert "Daftar Produk (Bahan Umpan):" in data_bait["summary_text"]


def test_products_endpoint_rejects_invalid_category():
    """Verify GET /products returns 400 for unsupported category filter."""
    res = client.get("/products?category=pancing_liar", headers=AUTH_HEADERS)
    assert res.status_code == 400
    assert "Kategori produk tidak valid" in res.json()["detail"]
