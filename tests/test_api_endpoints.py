"""Integration and unit tests for messages and transactions API endpoints."""

from decimal import Decimal
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.domain.models import DomainProduct, TransactionType
from app.infrastructure.llm.extractor import ExtractedItem, ExtractedTransaction
from app.infrastructure.llm.router import MessageIntent, MessageRoutingResult
from app.main import app

client = TestClient(app)
AUTH_HEADERS = {"X-API-Key": settings.api_secret}


def test_api_endpoints_require_valid_api_key():
    """Verify endpoints reject requests without or with invalid X-API-Key."""
    # Test POST /messages without key
    res1 = client.post(
        "/messages", json={"channel": "telegram", "chat_id": "1", "text": "halo"}
    )
    assert res1.status_code == 401

    # Test with invalid key
    res2 = client.post(
        "/messages",
        headers={"X-API-Key": "wrong-secret"},
        json={"channel": "telegram", "chat_id": "1", "text": "halo"},
    )
    assert res2.status_code == 401

    # Test POST /transactions/1/confirm without key
    res3 = client.post("/transactions/1/confirm")
    assert res3.status_code == 401

    # Test POST /transactions/1/reject without key
    res4 = client.post("/transactions/1/reject")
    assert res4.status_code == 401


def test_post_messages_intent_other():
    """Verify general chat returns informative response without drafting transaction."""
    with patch("app.services.message_service.get_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        mock_msg = type("MockMsg", (), {"id": 101})()
        mock_session.add.return_value = None

        with patch(
            "app.services.message_service.get_message_router"
        ) as mock_router_getter:
            mock_router = mock_router_getter.return_value
            mock_router.route.return_value = MessageRoutingResult(
                intent=MessageIntent.OTHER,
                confidence=0.99,
            )

            res = client.post(
                "/messages",
                headers=AUTH_HEADERS,
                json={
                    "channel": "telegram",
                    "chat_id": "user123",
                    "text": "selamat pagi",
                },
            )

            assert res.status_code == 200
            data = res.json()
            assert data["intent"] == "other"
            assert "bot pencatatan" in data["reply_text"]
            assert data["pending_transaction_id"] is None


def test_post_messages_intent_question():
    """Verify question chat returns informative response without drafting transaction."""
    with patch("app.services.message_service.get_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        mock_msg = type("MockMsg", (), {"id": 102})()
        mock_session.add.return_value = None

        with patch(
            "app.services.message_service.get_message_router"
        ) as mock_router_getter:
            mock_router = mock_router_getter.return_value
            mock_router.route.return_value = MessageRoutingResult(
                intent=MessageIntent.QUESTION,
                confidence=0.95,
            )

            res = client.post(
                "/messages",
                headers=AUTH_HEADERS,
                json={
                    "channel": "telegram",
                    "chat_id": "user123",
                    "text": "stok pelet sisa berapa?",
                },
            )

            assert res.status_code == 200
            data = res.json()
            assert data["intent"] == "question"
            assert "Task 10" in data["reply_text"]
            assert data["pending_transaction_id"] is None


def test_post_messages_unrecognized_product_asks_clarification():
    """Verify unrecognized product returns clarification question without creating transaction."""
    with patch("app.services.message_service.get_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value

        with (
            patch(
                "app.services.message_service.get_message_router"
            ) as mock_router_getter,
            patch(
                "app.services.message_service.TransactionExtractor"
            ) as mock_extractor_cls,
            patch(
                "app.services.message_service.ProductRepository"
            ) as mock_prod_repo_cls,
        ):

            mock_router = mock_router_getter.return_value
            mock_router.route.return_value = MessageRoutingResult(
                intent=MessageIntent.SALE,
                confidence=0.95,
            )

            mock_prod_repo = mock_prod_repo_cls.return_value
            mock_prod_repo.get_all.return_value = [
                DomainProduct(
                    id=1,
                    name="Pelet Jitu",
                    category="bahan_umpan",
                    unit="bungkus",
                    sell_price=Decimal("3000.00"),
                    avg_cost=Decimal("2500.00"),
                    stock_qty=10,
                    min_stock=2,
                )
            ]

            mock_extractor = mock_extractor_cls.return_value
            mock_extractor.extract.return_value = ExtractedTransaction(
                type=TransactionType.SALE,
                items=[
                    ExtractedItem(
                        product_id=None,  # Produk tidak terdaftar
                        product_name_raw="joran tegek carbon 540",
                        qty=1,
                        unit_price=Decimal("150000.00"),
                    )
                ],
            )

            res = client.post(
                "/messages",
                headers=AUTH_HEADERS,
                json={
                    "channel": "telegram",
                    "chat_id": "user123",
                    "text": "laku joran tegek 1",
                },
            )

            assert res.status_code == 200
            data = res.json()
            assert data["intent"] == "sale"
            assert "barang mana yang dimaksud" in data["reply_text"]
            assert "joran tegek carbon 540" in data["reply_text"]
            assert data["pending_transaction_id"] is None


def test_post_messages_sale_creates_pending_transaction():
    """Verify valid sale chat creates pending transaction with correct confirmation template."""
    with patch("app.services.message_service.get_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value

        with (
            patch(
                "app.services.message_service.get_message_router"
            ) as mock_router_getter,
            patch(
                "app.services.message_service.TransactionExtractor"
            ) as mock_extractor_cls,
            patch(
                "app.services.message_service.ProductRepository"
            ) as mock_prod_repo_cls,
            patch(
                "app.services.message_service.TransactionRepository"
            ) as mock_tx_repo_cls,
        ):

            mock_router = mock_router_getter.return_value
            mock_router.route.return_value = MessageRoutingResult(
                intent=MessageIntent.SALE,
                confidence=0.98,
            )

            mock_prod = DomainProduct(
                id=1,
                name="Pelet Jitu",
                category="bahan_umpan",
                unit="bungkus",
                sell_price=Decimal("3000.00"),
                avg_cost=Decimal("2500.00"),
                stock_qty=10,
                min_stock=2,
            )
            mock_prod_repo = mock_prod_repo_cls.return_value
            mock_prod_repo.get_all.return_value = [mock_prod]

            mock_extractor = mock_extractor_cls.return_value
            mock_extractor.extract.return_value = ExtractedTransaction(
                type=TransactionType.SALE,
                items=[
                    ExtractedItem(
                        product_id=1,
                        product_name_raw="pelet jitu",
                        qty=2,
                        unit_price=Decimal("3000.00"),
                    )
                ],
            )

            mock_tx_repo = mock_tx_repo_cls.return_value
            mock_tx = type("MockTx", (), {"id": 88})()
            mock_tx_repo.create_pending_transaction.return_value = mock_tx

            res = client.post(
                "/messages",
                headers=AUTH_HEADERS,
                json={
                    "channel": "telegram",
                    "chat_id": "user123",
                    "text": "laku pelet jitu 2",
                },
            )

            assert res.status_code == 200
            data = res.json()
            assert data["intent"] == "sale"
            assert data["pending_transaction_id"] == 88
            assert "Catatan Penjualan:" in data["reply_text"]
            assert (
                "Pelet Jitu: 2 bungkus x Rp 3,000.00 = Rp 6,000.00"
                in data["reply_text"]
            )
            assert 'Ketik "Benar" untuk konfirmasi' in data["reply_text"]


def test_transactions_confirm_and_reject_api():
    """Verify /transactions/{id}/confirm and reject endpoints call service properly."""
    with patch("app.api.transactions.confirm_transaction") as mock_confirm:
        mock_confirm.return_value = {
            "transaction_id": 42,
            "status": "confirmed",
            "summary_text": "Penjualan berhasil dikonfirmasi.",
            "remaining_stock": [
                {
                    "product_id": 1,
                    "product_name": "Pelet Jitu",
                    "stock_qty": 8,
                    "unit": "bungkus",
                }
            ],
        }

        res = client.post("/transactions/42/confirm", headers=AUTH_HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "confirmed"
        assert data["remaining_stock"][0]["stock_qty"] == 8

    with patch("app.api.transactions.reject_transaction") as mock_reject:
        mock_reject.return_value = {
            "transaction_id": 42,
            "status": "rejected",
            "message": "Transaksi ID 42 berhasil dibatalkan.",
        }

        res = client.post("/transactions/42/reject", headers=AUTH_HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "rejected"
