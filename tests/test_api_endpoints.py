"""Integration and unit tests for messages and transactions API endpoints."""

from decimal import Decimal
import secrets
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app.api.messages import (
    MAX_CHANNEL_LENGTH,
    MAX_CHAT_ID_LENGTH,
    MAX_IMAGE_BASE64_LENGTH,
    MAX_TEXT_LENGTH,
)
from app.api.security import verify_api_key
from app.config import settings
from app.domain.models import DomainProduct
from app.infrastructure.llm.extractor import (
    ExtractedItem,
    ExtractedTransaction,
    ExtractedTransactionType,
)
from app.infrastructure.llm.router import MessageIntent, MessageRoutingResult
from app.main import app
from app.services.message_service import _format_currency
from app.services.transaction_service import InvalidStatusError

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


def test_api_rejects_empty_server_api_secret():
    """Verify protected endpoints fail fast when API_SECRET is not configured."""
    with patch("app.api.security.settings") as mock_settings:
        mock_settings.api_secret = ""
        with pytest.raises(RuntimeError, match="API_SECRET is empty"):
            client.post(
                "/messages",
                headers={"X-API-Key": "anything"},
                json={"channel": "telegram", "chat_id": "1", "text": "halo"},
            )


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
            assert "bot pencatatan Toko Pancing Bapak" in data["reply_text"]
            assert data["pending_transaction_id"] is None
            assert data["show_main_menu"] is False


def test_post_messages_start_and_menu_commands_return_welcome_without_llm():
    """Verify /start and /menu return welcome guide and main menu flag without LLM."""
    with patch("app.services.message_service.get_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        mock_session.add.return_value = None

        with patch(
            "app.services.message_service.get_message_router"
        ) as mock_router_getter:
            for command_text in ("/start", "/menu"):
                res = client.post(
                    "/messages",
                    headers=AUTH_HEADERS,
                    json={
                        "channel": "telegram",
                        "chat_id": "user123",
                        "text": command_text,
                    },
                )

                mock_router_getter.return_value.route.assert_not_called()
                assert res.status_code == 200
                data = res.json()
                assert data["intent"] == "other"
                assert (
                    "Selamat datang di Asisten Toko Pancing Bapak" in data["reply_text"]
                )
                assert (
                    "Satu chat hanya untuk satu jenis transaksi" in data["reply_text"]
                )
                assert "Peralatan (" in data["reply_text"]
                assert "Bahan Umpan (" in data["reply_text"]
                assert "Cek Stok & Laporan:" in data["reply_text"]
                assert "1. Peralatan" not in data["reply_text"]
                assert "2. Bahan Umpan" not in data["reply_text"]
                assert "3. Cek Stok" not in data["reply_text"]
                assert "laku pelet jitu 2 bungkus" in data["reply_text"]
                assert data["pending_transaction_id"] is None
                assert data["show_main_menu"] is True


def test_format_currency_indonesian_no_decimals():
    """Verify Rupiah currency formatting uses Indonesian dot grouping without decimals."""
    assert _format_currency(Decimal("3000.00")) == "Rp3.000"
    assert _format_currency(Decimal("1250000")) == "Rp1.250.000"
    assert _format_currency(Decimal("0.00")) == "Rp0"


def test_post_messages_intent_question():
    """Verify question chat routes to AgentService without drafting transaction."""
    with patch("app.services.message_service.get_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        mock_session.add.return_value = None

        with (
            patch(
                "app.services.message_service.get_message_router"
            ) as mock_router_getter,
            patch("app.services.message_service.AgentService") as mock_agent_cls,
        ):
            mock_router = mock_router_getter.return_value
            mock_router.route.return_value = MessageRoutingResult(
                intent=MessageIntent.QUESTION,
                confidence=0.95,
            )
            mock_agent = mock_agent_cls.return_value
            mock_agent.answer_question.return_value = (
                "Sisa stok Pelet Jitu saat ini 18 bungkus."
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
            assert data["reply_text"] == "Sisa stok Pelet Jitu saat ini 18 bungkus."
            assert data["pending_transaction_id"] is None
            mock_agent.answer_question.assert_called_once_with(
                "stok pelet sisa berapa?", session=mock_session
            )


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
                type=ExtractedTransactionType.SALE,
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
                type=ExtractedTransactionType.SALE,
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
            assert data["show_main_menu"] is False
            assert "Konfirmasi PENJUALAN:" in data["reply_text"]
            assert "Pelet Jitu: 2 bungkus x Rp3.000 = Rp6.000" in data["reply_text"]
            assert "Total: Rp6.000" in data["reply_text"]
            assert 'Tekan tombol "Benar" untuk konfirmasi' in data["reply_text"]


def test_post_messages_purchase_creates_pending_transaction_with_pembelian_title():
    """Verify valid purchase chat creates pending transaction with PEMBELIAN title."""
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
            mock_router_getter.return_value.route.return_value = MessageRoutingResult(
                intent=MessageIntent.PURCHASE,
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
            mock_prod_repo_cls.return_value.get_all.return_value = [mock_prod]

            mock_extractor_cls.return_value.extract.return_value = ExtractedTransaction(
                type=ExtractedTransactionType.PURCHASE,
                items=[
                    ExtractedItem(
                        product_id=1,
                        product_name_raw="pelet jitu",
                        qty=5,
                        unit_price=Decimal("2500.00"),
                    )
                ],
            )

            mock_tx_repo = mock_tx_repo_cls.return_value
            mock_tx_repo.create_pending_transaction.return_value = type(
                "MockTx", (), {"id": 89}
            )()

            res = client.post(
                "/messages",
                headers=AUTH_HEADERS,
                json={
                    "channel": "telegram",
                    "chat_id": "user123",
                    "text": "beli pelet jitu 5 bungkus harga 2500",
                },
            )

            assert res.status_code == 200
            data = res.json()
            assert data["intent"] == "purchase"
            assert data["pending_transaction_id"] == 89
            assert "Konfirmasi PEMBELIAN:" in data["reply_text"]
            assert "Pelet Jitu: 5 bungkus x Rp2.500 = Rp12.500" in data["reply_text"]
            assert "Total: Rp12.500" in data["reply_text"]


def test_post_messages_mixed_transaction_rejected_without_saving():
    """Verify mixed sale+purchase message returns static template without saving transaction."""
    with patch("app.services.message_service.get_session") as mock_session_ctx:
        mock_session_ctx.return_value.__enter__.return_value = mock_session_ctx

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
            mock_router_getter.return_value.route.return_value = MessageRoutingResult(
                intent=MessageIntent.SALE,
                confidence=0.90,
            )
            mock_prod_repo_cls.return_value.get_all.return_value = []
            mock_extractor_cls.return_value.extract.return_value = ExtractedTransaction(
                type=ExtractedTransactionType.MIXED,
                items=[],
            )
            mock_tx_repo = mock_tx_repo_cls.return_value

            res = client.post(
                "/messages",
                headers=AUTH_HEADERS,
                json={
                    "channel": "telegram",
                    "chat_id": "user123",
                    "text": "laku pelet jitu 2 dan beli senar litecate 5 harga 29000",
                },
            )

            assert res.status_code == 200
            data = res.json()
            assert data["pending_transaction_id"] is None
            assert "Satu chat hanya untuk satu jenis transaksi" in data["reply_text"]
            assert "dua chat terpisah" in data["reply_text"]
            mock_tx_repo.create_pending_transaction.assert_not_called()


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


def test_transactions_confirm_invalid_status_returns_400():
    """Verify confirming an already-confirmed transaction maps InvalidStatusError to 400."""
    with patch(
        "app.api.transactions.confirm_transaction",
        side_effect=InvalidStatusError(
            "Transaksi ID 42 tidak dapat dikonfirmasi karena berstatus 'confirmed'."
        ),
    ):
        res = client.post("/transactions/42/confirm", headers=AUTH_HEADERS)
        assert res.status_code == 400
        assert "confirmed" in res.json()["detail"]


def test_verify_api_key_uses_constant_time_compare():
    """Verify verify_api_key compares secrets via secrets.compare_digest."""
    with patch(
        "app.api.security.secrets.compare_digest", wraps=secrets.compare_digest
    ) as mock_cmp:
        assert verify_api_key(settings.api_secret) == settings.api_secret
        assert mock_cmp.call_count == 1


def test_post_messages_rejects_oversized_fields_with_422():
    """Verify oversized channel, chat_id, text, or image_base64 returns HTTP 422."""
    oversized_payloads = [
        {
            "channel": "a" * (MAX_CHANNEL_LENGTH + 1),
            "chat_id": "user123",
            "text": "halo",
        },
        {
            "channel": "telegram",
            "chat_id": "1" * (MAX_CHAT_ID_LENGTH + 1),
            "text": "halo",
        },
        {
            "channel": "telegram",
            "chat_id": "user123",
            "text": "x" * (MAX_TEXT_LENGTH + 1),
        },
        {
            "channel": "telegram",
            "chat_id": "user123",
            "image_base64": "A" * (MAX_IMAGE_BASE64_LENGTH + 1),
        },
    ]

    for payload in oversized_payloads:
        res = client.post("/messages", headers=AUTH_HEADERS, json=payload)
        assert res.status_code == 422
