"""Unit tests for transaction extractor."""

from decimal import Decimal
from unittest.mock import MagicMock

from app.domain.models import DomainProduct, TransactionType
from app.infrastructure.llm.extractor import (
    ExtractedItem,
    ExtractedTransaction,
    TransactionExtractor,
)


def test_transaction_extractor_formats_catalog_and_calls_llm():
    """Verify TransactionExtractor formats catalog and parses extracted items."""
    mock_llm_client = MagicMock()
    mock_llm_client.generate_structured.return_value = ExtractedTransaction(
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

    extractor = TransactionExtractor(llm_client=mock_llm_client)
    sample_products = [
        DomainProduct(
            id=1,
            name="Pelet Jitu",
            category="bahan_umpan",
            unit="bungkus",
            sell_price=Decimal("3000.00"),
            avg_cost=Decimal("2500.00"),
            stock_qty=20,
            min_stock=5,
        )
    ]

    result = extractor.extract("laku pelet jitu 2 bungkus", sample_products)

    assert result.type == TransactionType.SALE
    assert len(result.items) == 1
    assert result.items[0].product_id == 1
    assert result.items[0].qty == 2
    assert result.items[0].unit_price == Decimal("3000.00")
    assert mock_llm_client.generate_structured.call_count == 1
