"""Base contracts and helpers for receipt image OCR providers."""

# pylint: disable=too-few-public-methods

from abc import ABC, abstractmethod
import base64
import binascii
from dataclasses import dataclass


class OCRError(Exception):
    """Raised when an OCR provider cannot decode or read a receipt image."""


@dataclass(frozen=True)
class OCRResult:
    """Result returned by a receipt OCR provider."""

    text: str
    provider: str
    model: str | None = None


def _detect_mime_type(raw_bytes: bytes) -> str:
    """Infer MIME type from decoded image magic bytes."""
    if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw_bytes.startswith(b"RIFF") and raw_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def normalize_image_base64(image_base64: str) -> tuple[str, str]:
    """Validate base64 payload or data URI and return (mime_type, clean_base64)."""
    cleaned = image_base64.strip()
    if not cleaned:
        raise OCRError("Foto nota kosong atau belum terunggah.")

    explicit_mime: str | None = None
    payload = cleaned
    if cleaned.startswith("data:"):
        header, sep, body = cleaned.partition(",")
        if not sep or ";base64" not in header:
            raise OCRError("Format data URL foto nota tidak valid.")
        explicit_mime = header[5:].split(";", 1)[0].strip().lower()
        if not explicit_mime.startswith("image/"):
            raise OCRError(f"Tipe media bukan gambar: {explicit_mime}.")
        payload = body.strip()

    compact = "".join(payload.split())
    try:
        decoded = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise OCRError("Data base64 foto nota tidak valid.") from exc

    if not decoded:
        raise OCRError("File foto nota kosong.")

    mime_type = explicit_mime or _detect_mime_type(decoded)
    return mime_type, compact


class BaseOCRProvider(ABC):
    """Abstract interface for converting a receipt image into plain text."""

    @abstractmethod
    def extract_text(self, image_base64: str) -> OCRResult:
        """Transcribe receipt text from a base64-encoded image."""
