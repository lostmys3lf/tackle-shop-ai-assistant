"""Message routing module for intent classification via LLM or Jev (Cloudflare Workers AI)."""

# pylint: disable=too-few-public-methods

import logging
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.config import Settings, settings as default_settings
from app.infrastructure.llm.client import LLMClient, LLMRequest, get_llm_client
from app.infrastructure.llm.exceptions import LLMError

logger = logging.getLogger(__name__)


class MessageIntent(str, Enum):
    """Message classification intent categories."""

    SALE = "sale"
    PURCHASE = "purchase"
    QUESTION = "question"
    OTHER = "other"


class MessageRoutingResult(BaseModel):
    """Result of message classification with intent and confidence score."""

    intent: MessageIntent
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class BaseMessageRouter(ABC):
    """Abstract base class for message classification routers."""

    @abstractmethod
    def route(self, text: str) -> MessageRoutingResult:
        """Classify message text into an intent."""


class LLMMessageRouter(BaseMessageRouter):
    """Message router using LLM structured completion."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        prompt_path: Path | None = None,
    ) -> None:
        """Initialize LLM router with client and prompt file."""
        self.client = llm_client or get_llm_client()
        if prompt_path is None:
            # Default lokasi file prompt router
            base_dir = Path(__file__).resolve().parent.parent.parent.parent
            prompt_path = base_dir / "prompts" / "router_v1.txt"
        self.prompt_path = prompt_path
        self._prompt_template = self._load_prompt()

    def _load_prompt(self) -> str:
        """Load router prompt text from file."""
        if not self.prompt_path.exists():
            raise FileNotFoundError(f"Prompt file not found at: {self.prompt_path}")
        return self.prompt_path.read_text(encoding="utf-8").strip()

    def route(self, text: str) -> MessageRoutingResult:
        """Classify message text using LLM."""
        req = LLMRequest(
            messages=[
                {"role": "system", "content": self._prompt_template},
                {"role": "user", "content": text},
            ],
            prompt_version="router_v1",
            name="message_intent_routing",
            temperature=0.0,
            max_tokens=250,
        )
        return self.client.generate_structured(req, MessageRoutingResult)


class JevMessageRouter(BaseMessageRouter):
    """Message router using Cloudflare Workers AI with model typesafe/jev."""

    def __init__(
        self,
        account_id: str | None = None,
        api_token: str | None = None,
        model_id: str = "typesafe/jev",
        http_client: httpx.Client | None = None,
    ) -> None:
        """Initialize Jev router with Cloudflare credentials."""
        self.account_id = (
            default_settings.cloudflare_account_id if account_id is None else account_id
        )
        self.api_token = (
            default_settings.cloudflare_api_token if api_token is None else api_token
        )
        self.model_id = model_id
        self._http_client = http_client

    def _get_client(self) -> httpx.Client:
        """Return injected HTTP client or create a new configured client."""
        if self._http_client is not None:
            return self._http_client
        return httpx.Client(timeout=15.0)

    def route(self, text: str) -> MessageRoutingResult:
        """Classify message text via Cloudflare Workers AI REST API."""
        if not self.account_id or not self.api_token:
            raise LLMError(
                "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN must be configured for Jev router"
            )

        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run"
        headers = {"Authorization": f"Bearer {self.api_token}"}
        criteria = {
            "sale": "Penjualan barang dagang ke pelanggan",
            "purchase": "Pembelian stok barang dagang dari supplier",
            "question": "Pertanyaan mengenai data toko, stok, atau laporan penjualan",
            "other": "Pesan di luar pencatatan toko atau percakapan umum",
        }
        payload: dict[str, Any] = {
            "model": self.model_id,
            "input": {
                "state": text,
                "questions": {
                    "intent": {
                        "type": "choice",
                        "instructions": (
                            "Klasifikasikan jenis pesan ini ke salah satu pilihan yang tersedia."
                        ),
                        "criteria": criteria,
                    }
                },
            },
        }

        client = self._get_client()
        try:
            response = client.post(url, headers=headers, json=payload)
        except Exception as exc:
            raise LLMError(
                f"HTTP request to Cloudflare Workers AI failed: {exc}"
            ) from exc

        if response.status_code != 200:
            err_msg = (
                f"Cloudflare Workers AI returned status {response.status_code}: "
                f"{response.text[:300]}"
            )
            raise LLMError(err_msg)

        data = response.json()
        if not data.get("success", False):
            raise LLMError(
                f"Cloudflare Workers AI reported failure: {data.get('errors')}"
            )

        return self._parse_jev_response(data)

    def _parse_jev_response(self, data: dict[str, Any]) -> MessageRoutingResult:
        """Extract intent and confidence from Jev response schema."""
        result = data.get("result", {})
        intent_data = result.get("intent", {})

        raw_intent = (
            intent_data.get("response")
            or intent_data.get("value")
            or intent_data.get("answer")
            or result.get("response")
        )

        if not raw_intent:
            raise LLMError(
                f"Unexpected Jev response format without intent value: {result}"
            )

        # Normalisasi ke enum MessageIntent
        try:
            intent = MessageIntent(str(raw_intent).lower().strip())
        except ValueError as exc:
            raise LLMError(
                f"Unsupported intent value returned by Jev: '{raw_intent}'"
            ) from exc

        probability = float(
            intent_data.get(
                "probability",
                intent_data.get("score", intent_data.get("confidence", 1.0)),
            )
        )
        # Pastikan confidence dalam rentang 0.0 - 1.0
        confidence = max(0.0, min(1.0, probability))

        return MessageRoutingResult(intent=intent, confidence=confidence)


class FallbackMessageRouter(BaseMessageRouter):
    """Router wrapper that falls back to a secondary router if the primary router fails."""

    def __init__(
        self,
        primary: BaseMessageRouter,
        fallback: BaseMessageRouter,
    ) -> None:
        """Initialize fallback router with primary and secondary implementations."""
        self.primary = primary
        self.fallback = fallback

    def route(self, text: str) -> MessageRoutingResult:
        """Route message with automatic fallback if primary router fails."""
        try:
            return self.primary.route(text)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Router utama '%s' gagal (%s). Beralih ke router fallback '%s'...",
                self.primary.__class__.__name__,
                exc,
                self.fallback.__class__.__name__,
            )
            return self.fallback.route(text)


def get_message_router(cfg: Settings | None = None) -> BaseMessageRouter:
    """Create and configure message router based on application configuration."""
    config = cfg or default_settings
    provider = config.router_provider.lower().strip()

    llm_router = LLMMessageRouter()

    if provider == "llm":
        return llm_router

    if provider == "jev":
        jev_router = JevMessageRouter(
            account_id=config.cloudflare_account_id,
            api_token=config.cloudflare_api_token,
            model_id=config.router_jev_model,
        )
        # Jev otomatis fallback ke LLM jika terjadi kegagalan
        return FallbackMessageRouter(primary=jev_router, fallback=llm_router)

    raise ValueError(
        f"Unsupported ROUTER_PROVIDER: '{config.router_provider}'. Expected 'jev' or 'llm'."
    )
