"""Receipt OCR provider using Groq Vision via the centralized LLMClient."""

# pylint: disable=too-few-public-methods

from pathlib import Path

from app.config import settings as default_settings
from app.infrastructure.llm.client import (
    LLMAllModelsFailedError,
    LLMClient,
    LLMError,
    LLMRequest,
    LLMRateLimitError,
    get_llm_client,
)
from app.infrastructure.ocr.base import (
    BaseOCRProvider,
    OCRError,
    OCRResult,
    normalize_image_base64,
)

DEFAULT_OCR_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "ocr_v1.txt"
)
PROMPT_VERSION = "ocr_v1"
UNREADABLE_MARKER = "NOTA_TIDAK_TERBACA"


class GroqVisionOCRProvider(BaseOCRProvider):
    """OCR provider that transcribes receipt photos via Groq Vision model."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        model: str | None = None,
        prompt_path: Path | None = None,
    ) -> None:
        """Initialize Groq Vision OCR provider with client, model, and prompt."""
        self.client = llm_client or get_llm_client()
        self.model = (model or default_settings.ocr_vision_model).strip()
        self.prompt_path = prompt_path or DEFAULT_OCR_PROMPT_PATH
        self._prompt_text = self._load_prompt()

    def _load_prompt(self) -> str:
        """Load versioned OCR instruction prompt from prompts/ directory."""
        if not self.prompt_path.exists():
            raise FileNotFoundError(
                f"File prompt OCR tidak ditemukan: {self.prompt_path}"
            )
        return self.prompt_path.read_text(encoding="utf-8").strip()

    def extract_text(self, image_base64: str) -> OCRResult:
        """Transcribe receipt image via Groq Vision chat completion."""
        mime_type, clean_b64 = normalize_image_base64(image_base64)
        messages = [
            {"role": "system", "content": self._prompt_text},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Salin seluruh isi foto nota ini secara akurat.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{clean_b64}"},
                    },
                ],
            },
        ]
        request = LLMRequest(
            messages=messages,
            model=self.model,
            prompt_version=PROMPT_VERSION,
            max_tokens=1500,
            temperature=0.0,
            name="ocr_groq_vision",
            metadata={"provider": "groq_vision", "mime_type": mime_type},
        )
        try:
            raw_text = self.client.generate_text(request)
        except (LLMError, LLMRateLimitError, LLMAllModelsFailedError) as exc:
            raise OCRError(f"Gagal membaca foto nota lewat Groq Vision: {exc}") from exc

        cleaned = raw_text.strip()
        if not cleaned or UNREADABLE_MARKER in cleaned:
            raise OCRError(
                "Foto nota tidak terbaca. Mohon kirim foto yang lebih jelas dan terang."
            )

        return OCRResult(text=cleaned, provider="groq_vision", model=self.model)
