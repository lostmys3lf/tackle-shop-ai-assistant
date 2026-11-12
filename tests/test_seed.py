"""Unit tests for database seed and CSV validation."""

import pytest
from app.infrastructure.db.seed import (
    EmptySeedFileError,
    load_products_from_csv,
)


def test_seed_missing_file_raises_clear_error(tmp_path):
    """Ensure missing CSV seed file raises EmptySeedFileError with clear message."""
    missing_file = tmp_path / "non_existent.csv"
    with pytest.raises(EmptySeedFileError, match="does not exist"):
        load_products_from_csv(missing_file)


def test_seed_zero_byte_file_raises_clear_error(tmp_path):
    """Ensure empty (0 bytes) CSV file raises EmptySeedFileError with clear message."""
    empty_file = tmp_path / "empty.csv"
    empty_file.write_text("", encoding="utf-8")
    with pytest.raises(EmptySeedFileError, match="completely empty"):
        load_products_from_csv(empty_file)


def test_seed_header_only_file_raises_clear_error(tmp_path):
    """Ensure CSV file with headers but no rows raises EmptySeedFileError with clear message."""
    header_only = tmp_path / "header_only.csv"
    header_only.write_text(
        "name,category,unit,purchase_price,sell_price,stock_qty,min_stock\n",
        encoding="utf-8",
    )
    with pytest.raises(EmptySeedFileError, match="contains header but no data rows"):
        load_products_from_csv(header_only)


def test_seed_valid_csv_file_parses_properly(tmp_path):
    """Ensure valid CSV rows are parsed with correct decimal types and values."""
    valid_csv = tmp_path / "valid.csv"
    valid_csv.write_text(
        "name,category,unit,purchase_price,sell_price,stock_qty,min_stock\n"
        "Senar Test,peralatan,gulung,10000.50,15000.00,5,2\n",
        encoding="utf-8",
    )
    records = load_products_from_csv(valid_csv)
    assert len(records) == 1
    assert records[0]["name"] == "Senar Test"
    assert records[0]["stock_qty"] == 5
    assert str(records[0]["purchase_price"]) == "10000.50"
