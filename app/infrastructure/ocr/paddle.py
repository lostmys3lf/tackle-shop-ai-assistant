"""Optional local receipt OCR provider adapter using PaddleOCR."""

# pylint: disable=too-few-public-methods

import base64
from pathlib import Path
import tempfile
from typing import Any

from app.infrastructure.ocr.base import (
    BaseOCRProvider,
    OCRError,
    OCRResult,
    normalize_image_base64,
)

MIME_SUFFIX_MAP = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


def _parse_paddle_lines(raw_result: Any) -> str:
    """Flatten PaddleOCR page/line predictions into plain newline-separated text."""
    if not raw_result or not isinstance(raw_result, list):
        return ""

    lines: list[str] = []
    for page in raw_result:
        if not isinstance(page, list):
            continue
        for entry in page:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            text_info = entry[1]
            if isinstance(text_info, (list, tuple)) and text_info:
                text_value = str(text_info[0]).strip()
                if text_value:
                    lines.append(text_value)
    return "\n".join(lines).strip()


class PaddleOCRProvider(BaseOCRProvider):
    """Local OCR adapter wrapping PaddleOCR with lazy optional import."""

    def __init__(self, lang: str = "id") -> None:
        """Store language setting; PaddleOCR engine loads lazily on first call."""
        self.lang = lang
        self._engine: Any = None

    def _load_engine(self) -> Any:
        """Import and initialize PaddleOCR lazily so Docker stays lightweight."""
        if self._engine is not None:
            return self._engine

        try:
            # Import dinamis agar image API tidak wajib memasang paddleocr secara default
            from paddleocr import (  # pylint: disable=import-error,import-outside-toplevel
                PaddleOCR,
            )
        except ImportError as exc:
            raise OCRError(
                "Provider OCR 'paddle' memerlukan paket opsional 'paddleocr' yang belum terpasang."
            ) from exc

        self._engine = PaddleOCR(use_angle_cls=True, lang=self.lang, show_log=False)
        return self._engine

    def extract_text(self, image_base64: str) -> OCRResult:
        """Transcribe receipt image locally via PaddleOCR."""
        mime_type, clean_b64 = normalize_image_base64(image_base64)
        engine = self._load_engine()
        image_bytes = base64.b64decode(clean_b64)
        suffix = MIME_SUFFIX_MAP.get(mime_type, ".jpg")

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
                tmp_file.write(image_bytes)
                tmp_path = Path(tmp_file.name)

            raw_result = engine.ocr(str(tmp_path), cls=True)
        except OCRError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise OCRError(f"PaddleOCR gagal membaca foto nota: {exc}") from exc
        finally:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

        text = _parse_paddle_lines(raw_result)
        if not text:
            raise OCRError(
                "Foto nota tidak terbaca oleh PaddleOCR. Mohon kirim foto lebih jelas."
            )

        return OCRResult(text=text, provider="paddle", model="PaddleOCR-VL")
