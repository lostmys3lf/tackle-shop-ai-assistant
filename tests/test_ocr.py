"""Unit and integration tests for receipt photo OCR pipeline (Task 13)."""

import base64
from decimal import Decimal
import sys
import types
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from app.config import Settings, settings
from app.domain.models import DomainProduct, TransactionSource
from app.infrastructure.llm.client import LLMError
from app.infrastructure.llm.extractor import (
    ExtractedItem,
    ExtractedTransaction,
    ExtractedTransactionType,
)
from app.infrastructure.ocr import (
    GroqVisionOCRProvider,
    OCRError,
    OCRResult,
    PaddleOCRProvider,
    get_ocr_provider,
)
from app.infrastructure.ocr.base import normalize_image_base64
from app.main import app

client = TestClient(app)
AUTH_HEADERS = {"X-API-Key": settings.api_secret}
SAMPLE_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-png-content").decode("ascii")
SAMPLE_JPG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0fake-jpg-content").decode("ascii")


def test_normalize_image_base64_detects_mime_and_validates():
    """Verify MIME detection from raw bytes, data URIs, and invalid base64 rejection."""
    png_mime, png_clean = normalize_image_base64(SAMPLE_PNG_B64)
    assert png_mime == "image/png"
    assert png_clean == SAMPLE_PNG_B64

    jpg_mime, _ = normalize_image_base64(f"data:image/jpeg;base64,{SAMPLE_JPG_B64}")
    assert jpg_mime == "image/jpeg"

    with pytest.raises(OCRError):
        normalize_image_base64("   ")

    with pytest.raises(OCRError):
        normalize_image_base64("not-valid-base64!!!")


def test_groq_vision_ocr_provider_calls_llm_client_and_returns_text():
    """Verify GroqVisionOCRProvider routes vision prompt & image_url via LLMClient."""
    mock_client = MagicMock()
    mock_client.generate_text.return_value = (
        "NOTA PENJUALAN\n1. Pelet Jitu 3 bungkus @ Rp 3.000"
    )
    provider = GroqVisionOCRProvider(
        llm_client=mock_client,
        model="qwen/qwen3.8-27b",
    )

    result = provider.extract_text(SAMPLE_PNG_B64)

    assert result.provider == "groq_vision"
    assert result.model == "qwen/qwen3.8-27b"
    assert "Pelet Jitu" in result.text

    req = mock_client.generate_text.call_args.args[0]
    assert req.model == "qwen/qwen3.8-27b"
    assert req.prompt_version == "ocr_v1"
    assert req.name == "ocr_groq_vision"
    assert (
        req.messages[1]["content"][1]["image_url"]["url"]
        == f"data:image/png;base64,{SAMPLE_PNG_B64}"
    )


def test_groq_vision_ocr_provider_raises_on_unreadable_or_llm_error():
    """Verify GroqVisionOCRProvider converts unreadable marker and LLM errors to OCRError."""
    mock_client = MagicMock()
    mock_client.generate_text.return_value = "NOTA_TIDAK_TERBACA"
    provider = GroqVisionOCRProvider(llm_client=mock_client, model="qwen/qwen3.8-27b")

    with pytest.raises(OCRError, match="tidak terbaca"):
        provider.extract_text(SAMPLE_PNG_B64)

    mock_client.generate_text.side_effect = LLMError("Vision gateway error")
    with pytest.raises(OCRError, match="Groq Vision"):
        provider.extract_text(SAMPLE_PNG_B64)


def test_paddle_ocr_provider_lazy_imports_and_reads_lines():
    """Verify PaddleOCRProvider imports paddleocr lazily and flattens detected lines."""
    provider = PaddleOCRProvider(lang="id")
    assert provider._engine is None  # pylint: disable=protected-access

    fake_engine = MagicMock()
    fake_engine.ocr.return_value = [
        [
            [[[0, 0], [10, 0], [10, 10], [0, 10]], ("NOTA PENJUALAN", 0.99)],
            [[[0, 12], [10, 12], [10, 22], [0, 22]], ("Pelet Jitu 3 bks", 0.97)],
        ]
    ]
    fake_module = types.ModuleType("paddleocr")
    fake_module.PaddleOCR = MagicMock(return_value=fake_engine)

    with patch.dict(sys.modules, {"paddleocr": fake_module}):
        result = provider.extract_text(SAMPLE_JPG_B64)

    assert result.provider == "paddle"
    assert result.model == "PaddleOCR-VL"
    assert result.text == "NOTA PENJUALAN\nPelet Jitu 3 bks"
    fake_module.PaddleOCR.assert_called_once()


def test_paddle_ocr_provider_raises_clear_error_when_uninstalled():
    """Verify PaddleOCRProvider raises actionable OCRError if paddleocr is absent."""
    provider = PaddleOCRProvider(lang="id")
    with patch.dict(sys.modules, {"paddleocr": None}):
        with pytest.raises(OCRError, match="paddleocr"):
            provider.extract_text(SAMPLE_PNG_B64)


def test_get_ocr_provider_selects_configured_backend():
    """Verify OCR factory supports groq_vision, paddle, and rejects unknown values."""
    with patch("app.infrastructure.ocr.groq_vision.get_llm_client"):
        groq_provider = get_ocr_provider(
            Settings(ocr_provider="groq_vision", ocr_vision_model="qwen/qwen3.8-27b")
        )
        assert isinstance(groq_provider, GroqVisionOCRProvider)

    paddle_provider = get_ocr_provider(Settings(ocr_provider="paddle"))
    assert isinstance(paddle_provider, PaddleOCRProvider)

    with pytest.raises(ValueError, match="Provider OCR tidak dikenal"):
        get_ocr_provider(Settings(ocr_provider="tesseract"))


def test_post_messages_photo_creates_pending_transaction_with_photo_source():
    """Verify POST /messages with image_base64 runs OCR -> extractor -> photo pending draft."""
    mock_prod = DomainProduct(
        id=5,
        name="Pelet Jitu",
        category="bahan_umpan",
        unit="bungkus",
        sell_price=Decimal("3000.00"),
        avg_cost=Decimal("2500.00"),
        stock_qty=20,
        min_stock=5,
    )

    with (
        patch("app.services.message_service.get_session") as mock_session_ctx,
        patch("app.services.message_service.get_message_router"),
        patch("app.services.message_service.get_ocr_provider") as mock_ocr_getter,
        patch(
            "app.services.message_service.TransactionExtractor"
        ) as mock_extractor_cls,
        patch("app.services.message_service.ProductRepository") as mock_prod_repo_cls,
        patch("app.services.message_service.TransactionRepository") as mock_tx_repo_cls,
        patch("app.services.message_service.MessageRepository") as mock_msg_repo_cls,
    ):
        mock_session_ctx.return_value.__enter__.return_value = MagicMock()
        mock_msg_repo_cls.return_value.create_message.return_value = type(
            "MockMsg", (), {"id": 11}
        )()
        mock_ocr_getter.return_value.extract_text.return_value = OCRResult(
            text="NOTA PENJUALAN\nPelet Jitu 3 bungkus @ Rp 3.000",
            provider="groq_vision",
            model="qwen/qwen3.8-27b",
        )
        mock_prod_repo_cls.return_value.get_all.return_value = [mock_prod]
        mock_extractor_cls.return_value.extract.return_value = ExtractedTransaction(
            type=ExtractedTransactionType.SALE,
            items=[
                ExtractedItem(
                    product_id=5,
                    product_name_raw="Pelet Jitu",
                    qty=3,
                    unit_price=Decimal("3000.00"),
                )
            ],
        )
        mock_tx_repo_cls.return_value.create_pending_transaction.return_value = type(
            "MockTx", (), {"id": 91}
        )()

        res = client.post(
            "/messages",
            headers=AUTH_HEADERS,
            json={
                "channel": "telegram",
                "chat_id": "1191657048",
                "text": "Nota penjualan hari ini",
                "image_base64": SAMPLE_PNG_B64,
            },
        )

    assert res.status_code == 200
    data = res.json()
    assert data["intent"] == "sale"
    assert data["pending_transaction_id"] == 91
    assert "Konfirmasi PENJUALAN:" in data["reply_text"]
    assert "Pelet Jitu: 3 bungkus x Rp3.000 = Rp9.000" in data["reply_text"]

    # Pastikan source transaksi tercatat sebagai 'photo' beserta caption + hasil OCR
    create_kwargs = (
        mock_tx_repo_cls.return_value.create_pending_transaction.call_args.kwargs
    )
    assert create_kwargs["source"] == TransactionSource.PHOTO.value
    assert "Catatan pemilik: Nota penjualan hari ini" in create_kwargs["raw_text"]
    assert "Pelet Jitu 3 bungkus" in create_kwargs["raw_text"]


def test_post_messages_photo_handles_ocr_error_gracefully():
    """Verify unreadable receipt returns friendly message without saving pending tx."""
    with (
        patch("app.services.message_service.get_session") as mock_session_ctx,
        patch("app.services.message_service.get_message_router"),
        patch("app.services.message_service.get_ocr_provider") as mock_ocr_getter,
        patch("app.services.message_service.TransactionRepository") as mock_tx_repo_cls,
        patch("app.services.message_service.MessageRepository") as mock_msg_repo_cls,
    ):
        mock_session_ctx.return_value.__enter__.return_value = MagicMock()
        mock_msg_repo_cls.return_value.create_message.return_value = type(
            "MockMsg", (), {"id": 12}
        )()
        mock_ocr_getter.return_value.extract_text.side_effect = OCRError(
            "Foto nota tidak terbaca. Mohon kirim foto yang lebih jelas dan terang."
        )

        res = client.post(
            "/messages",
            headers=AUTH_HEADERS,
            json={
                "channel": "telegram",
                "chat_id": "1191657048",
                "text": "",
                "image_base64": SAMPLE_PNG_B64,
            },
        )

    assert res.status_code == 200
    data = res.json()
    assert data["pending_transaction_id"] is None
    assert "tidak terbaca" in data["reply_text"]
    mock_tx_repo_cls.return_value.create_pending_transaction.assert_not_called()
