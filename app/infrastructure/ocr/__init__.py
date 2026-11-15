"""Factory and exports for receipt OCR providers."""

from app.config import Settings
from app.config import settings as default_settings
from app.infrastructure.ocr.base import BaseOCRProvider, OCRError, OCRResult
from app.infrastructure.ocr.groq_vision import GroqVisionOCRProvider
from app.infrastructure.ocr.paddle import PaddleOCRProvider


def get_ocr_provider(cfg: Settings | None = None) -> BaseOCRProvider:
    """Create configured receipt OCR provider from OCR_PROVIDER setting."""
    config = cfg or default_settings
    provider = config.ocr_provider.strip().lower()

    if provider == "groq_vision":
        return GroqVisionOCRProvider(model=config.ocr_vision_model)
    if provider == "paddle":
        return PaddleOCRProvider()
    raise ValueError(f"Provider OCR tidak dikenal: {provider}")


__all__ = [
    "BaseOCRProvider",
    "GroqVisionOCRProvider",
    "OCRError",
    "OCRResult",
    "PaddleOCRProvider",
    "get_ocr_provider",
]
