"""Transaction extraction module using LLM structured generation."""

# pylint: disable=too-few-public-methods

from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field

from app.domain.models import DomainProduct, TransactionType
from app.infrastructure.llm.client import LLMClient, LLMRequest, get_llm_client

DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "prompts"
    / "extraction_v1.txt"
)


class ExtractedItem(BaseModel):
    """Extracted line item from transaction chat."""

    product_id: int | None = None
    product_name_raw: str
    qty: int = Field(default=1, ge=1)
    unit_price: Decimal | None = None


class ExtractedTransaction(BaseModel):
    """Structured transaction data extracted from chat text."""

    type: TransactionType
    items: list[ExtractedItem] = Field(default_factory=list)


class TransactionExtractor:
    """Extractor for transactions using prompt template and LLMClient."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        prompt_path: Path | None = None,
    ) -> None:
        """Initialize extractor with LLM client and prompt template file."""
        self.client = llm_client or get_llm_client()
        self.prompt_path = prompt_path or DEFAULT_PROMPT_PATH
        self._prompt_template = self._load_prompt()

    def _load_prompt(self) -> str:
        """Load prompt template from disk."""
        if not self.prompt_path.exists():
            raise FileNotFoundError(
                f"Extraction prompt not found at: {self.prompt_path}"
            )
        return self.prompt_path.read_text(encoding="utf-8").strip()

    def _format_catalog(self, products: list[DomainProduct]) -> str:
        """Format list of active products into text catalog for the prompt."""
        if not products:
            return "- (Belum ada produk di database)"

        lines: list[str] = []
        for p in products:
            p_id = p.id if p.id is not None else 0
            lines.append(
                f"- ID: {p_id} | Nama: {p.name} | Kategori: {p.category} | "
                f"Satuan: {p.unit} | Harga Jual: Rp {p.sell_price:,.2f}"
            )
        return "\n".join(lines)

    def extract(
        self,
        text: str,
        products: list[DomainProduct],
    ) -> ExtractedTransaction:
        """Extract structured transaction from text using product catalog."""
        catalog_text = self._format_catalog(products)
        system_content = self._prompt_template.replace(
            "{products_catalog}", catalog_text
        )

        req = LLMRequest(
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": text},
            ],
            prompt_version="extraction_v1",
            name="transaction_extraction",
            temperature=0.0,
            max_tokens=500,
        )

        return self.client.generate_structured(req, ExtractedTransaction)
