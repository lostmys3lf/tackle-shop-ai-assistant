"""Unit and integration tests for ReportRepository and AgentService."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.domain.models import AccountCode, AccountType
from app.infrastructure.db.models import (
    Account,
    Base,
    JournalEntry,
    JournalLine,
    Product,
    Transaction,
    TransactionItem,
)
from app.infrastructure.db.repositories import ReportRepository
from app.infrastructure.llm.client import LLMToolCall, LLMToolResponse
from app.services.agent_service import (
    AgentService,
    MAX_TOOL_ITERATIONS,
    strip_markdown_markers,
)


@pytest.fixture
def seeded_db_session():
    """Provide SQLite in-memory DB seeded with products, transactions, and journals."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    session.add_all(
        [
            Account(
                code=AccountCode.CASH.value, name="Kas", type=AccountType.ASSET.value
            ),
            Account(
                code=AccountCode.INVENTORY.value,
                name="Persediaan Barang Dagang",
                type=AccountType.ASSET.value,
            ),
            Account(
                code=AccountCode.SALES_REVENUE.value,
                name="Penjualan",
                type=AccountType.REVENUE.value,
            ),
            Account(
                code=AccountCode.COGS.value,
                name="Harga Pokok Penjualan",
                type=AccountType.EXPENSE.value,
            ),
        ]
    )

    p1 = Product(
        name="Pelet Jitu",
        category="bahan_umpan",
        unit="bungkus",
        sell_price=Decimal("3000.00"),
        avg_cost=Decimal("2500.00"),
        stock_qty=18,
        min_stock=5,
    )
    p2 = Product(
        name="Senar Litecate N Nylon 150 m",
        category="peralatan",
        unit="gulung",
        sell_price=Decimal("35000.00"),
        avg_cost=Decimal("29000.00"),
        stock_qty=2,
        min_stock=3,
    )
    p3 = Product(
        name="Umpan Tepung Bogor",
        category="bahan_umpan",
        unit="bungkus",
        sell_price=Decimal("7000.00"),
        avg_cost=Decimal("5800.00"),
        stock_qty=15,
        min_stock=5,
    )
    session.add_all([p1, p2, p3])
    session.flush()

    # Tx 1: Confirmed sale on 2026-11-13 WIB (2026-11-13 05:00 UTC)
    tx1 = Transaction(
        type="sale",
        status="confirmed",
        source="chat",
        raw_text="laku pelet jitu 4 bungkus dan senar litecate 1 gulung",
        confirmed_at=datetime(2026, 11, 13, 5, 0, tzinfo=timezone.utc),
    )
    # Tx 2: Confirmed sale on 2026-11-13 00:30 WIB (2026-11-12 17:30 UTC - tests Jakarta tz!)
    tx2 = Transaction(
        type="sale",
        status="confirmed",
        source="chat",
        raw_text="laku pelet jitu 2 bungkus",
        confirmed_at=datetime(2026, 11, 12, 17, 30, tzinfo=timezone.utc),
    )
    # Tx 3: Pending sale (must be excluded from sales summary, top products, and P&L)
    tx3 = Transaction(
        type="sale",
        status="pending",
        source="chat",
        raw_text="laku umpan tepung bogor 10 bungkus",
        confirmed_at=None,
    )
    # Tx 4: Confirmed purchase (must be excluded from sales summary and top products)
    tx4 = Transaction(
        type="purchase",
        status="confirmed",
        source="chat",
        raw_text="beli pelet jitu 10 harga 2500",
        confirmed_at=datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc),
    )
    session.add_all([tx1, tx2, tx3, tx4])
    session.flush()

    session.add_all(
        [
            TransactionItem(
                transaction_id=tx1.id,
                product_id=p1.id,
                qty=4,
                unit_price=Decimal("3000.00"),
            ),
            TransactionItem(
                transaction_id=tx1.id,
                product_id=p2.id,
                qty=1,
                unit_price=Decimal("35000.00"),
            ),
            TransactionItem(
                transaction_id=tx2.id,
                product_id=p1.id,
                qty=2,
                unit_price=Decimal("3000.00"),
            ),
            TransactionItem(
                transaction_id=tx3.id,
                product_id=p3.id,
                qty=10,
                unit_price=Decimal("7000.00"),
            ),
            TransactionItem(
                transaction_id=tx4.id,
                product_id=p1.id,
                qty=10,
                unit_price=Decimal("2500.00"),
            ),
        ]
    )

    je1 = JournalEntry(
        transaction_id=tx1.id,
        entry_date=date(2026, 11, 13),
        description="Penjualan tx1",
    )
    je2 = JournalEntry(
        transaction_id=tx2.id,
        entry_date=date(2026, 11, 13),
        description="Penjualan tx2",
    )
    session.add_all([je1, je2])
    session.flush()

    session.add_all(
        [
            # tx1 revenue: 12000 + 35000 = 47000; COGS: 10000 + 29000 = 39000
            JournalLine(
                journal_entry_id=je1.id,
                account_code=AccountCode.CASH.value,
                debit=Decimal("47000.00"),
                credit=Decimal("0.00"),
            ),
            JournalLine(
                journal_entry_id=je1.id,
                account_code=AccountCode.SALES_REVENUE.value,
                debit=Decimal("0.00"),
                credit=Decimal("47000.00"),
            ),
            JournalLine(
                journal_entry_id=je1.id,
                account_code=AccountCode.COGS.value,
                debit=Decimal("39000.00"),
                credit=Decimal("0.00"),
            ),
            JournalLine(
                journal_entry_id=je1.id,
                account_code=AccountCode.INVENTORY.value,
                debit=Decimal("0.00"),
                credit=Decimal("39000.00"),
            ),
            # tx2 revenue: 6000; COGS: 5000
            JournalLine(
                journal_entry_id=je2.id,
                account_code=AccountCode.CASH.value,
                debit=Decimal("6000.00"),
                credit=Decimal("0.00"),
            ),
            JournalLine(
                journal_entry_id=je2.id,
                account_code=AccountCode.SALES_REVENUE.value,
                debit=Decimal("0.00"),
                credit=Decimal("6000.00"),
            ),
            JournalLine(
                journal_entry_id=je2.id,
                account_code=AccountCode.COGS.value,
                debit=Decimal("5000.00"),
                credit=Decimal("0.00"),
            ),
            JournalLine(
                journal_entry_id=je2.id,
                account_code=AccountCode.INVENTORY.value,
                debit=Decimal("0.00"),
                credit=Decimal("5000.00"),
            ),
        ]
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()


def test_report_repository_queries_match_direct_sql(seeded_db_session):
    """Verify ReportRepository numbers match raw SQL calculations across all 4 queries."""
    repo = ReportRepository(seeded_db_session)

    # 1. Stock queries (specific token match + low stock + all products)
    pelet_stock = repo.get_stock("pelet")
    assert len(pelet_stock) == 1
    assert pelet_stock[0].stock_qty == 18

    token_stock = repo.get_stock("umpan bogor")
    assert len(token_stock) == 1
    assert token_stock[0].name == "Umpan Tepung Bogor"

    low_stock = repo.get_low_stock()
    assert [p.name for p in low_stock] == ["Senar Litecate N Nylon 150 m"]

    # 2. Sales summary on 2026-11-13 WIB includes tx1 + tx2 (ignores pending tx3 & purchase tx4)
    summary = repo.get_sales_summary(date(2026, 11, 13), date(2026, 11, 13))
    direct_total = seeded_db_session.execute(
        text(
            "SELECT COALESCE(SUM(ti.qty * ti.unit_price), 0) "
            "FROM transaction_items ti JOIN transactions t ON t.id = ti.transaction_id "
            "WHERE t.type = 'sale' AND t.status = 'confirmed'"
        )
    ).scalar_one()
    assert summary.total_sales == Decimal(str(direct_total)).quantize(Decimal("0.01"))
    assert summary.total_sales == Decimal("53000.00")
    assert summary.transaction_count == 2
    assert summary.total_items_sold == 7

    # 3. Top products ordered by sold quantity
    top = repo.get_top_products(date(2026, 11, 13), date(2026, 11, 13), limit=5)
    assert len(top) == 2
    assert top[0].product_name == "Pelet Jitu"
    assert top[0].total_qty == 6
    assert top[0].total_revenue == Decimal("18000.00")
    assert top[1].product_name == "Senar Litecate N Nylon 150 m"
    assert top[1].total_qty == 1

    # 4. Profit & loss from journal lines for 2026-11
    pnl = repo.get_profit_loss("2026-11")
    assert pnl.month == "2026-11"
    assert pnl.total_sales == Decimal("53000.00")
    assert pnl.total_cogs == Decimal("44000.00")
    assert pnl.gross_profit == Decimal("9000.00")


def test_agent_service_executes_tools_and_uses_repository_numbers(seeded_db_session):
    """Verify AgentService invokes real repository tools and receives exact formatted data."""
    mock_llm = MagicMock()
    mock_llm.generate_with_tools.side_effect = [
        LLMToolResponse(
            content="",
            tool_calls=[
                LLMToolCall(
                    id="call_1",
                    name="sales_summary",
                    arguments='{"start": "2026-11-13", "end": "2026-11-13"}',
                ),
                LLMToolCall(
                    id="call_2",
                    name="top_products",
                    arguments='{"start": "2026-11-13", "end": "2026-11-13", "limit": 3}',
                ),
            ],
        ),
        LLMToolResponse(
            content=(
                "Penjualan hari ini tercatat 2 transaksi sebesar Rp53.000 "
                "dengan produk terlaris Pelet Jitu (6 bungkus)."
            ),
            tool_calls=[],
        ),
    ]

    agent = AgentService(llm_client=mock_llm)
    run_res = agent.run_agent(
        "penjualan hari ini berapa dan apa yang paling laris?",
        session=seeded_db_session,
        today=date(2026, 11, 13),
    )

    assert run_res.tool_calls_made == ["sales_summary", "top_products"]
    assert run_res.tool_outputs[0]["output"]["total_sales_formatted"] == "Rp53.000"
    assert run_res.tool_outputs[0]["output"]["transaction_count"] == 2
    assert run_res.tool_outputs[1]["output"]["top_products"][0]["total_qty"] == 6
    assert "Rp53.000" in run_res.answer
    assert "Pelet Jitu" in run_res.answer

    # Verify tool responses were passed back cleanly in second LLM call
    second_call_req = mock_llm.generate_with_tools.call_args_list[1][0][0]
    tool_msgs = [m for m in second_call_req.messages if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    assert "Rp53.000" in tool_msgs[0]["content"]


def test_agent_service_handles_invalid_tool_args_and_max_iterations(seeded_db_session):
    """Verify agent gracefully recovers from bad tool args and caps iterations."""
    mock_llm = MagicMock()
    mock_llm.generate_with_tools.side_effect = [
        LLMToolResponse(
            content="",
            tool_calls=[
                LLMToolCall(
                    id="call_bad_json",
                    name="profit_loss",
                    arguments="not-json",
                ),
                LLMToolCall(
                    id="call_unknown",
                    name="nonexistent_tool",
                    arguments="{}",
                ),
            ],
        ),
        LLMToolResponse(
            content="",
            tool_calls=[
                LLMToolCall(
                    id="call_bad_month",
                    name="profit_loss",
                    arguments='{"month": "november"}',
                )
            ],
        ),
    ] + [
        LLMToolResponse(
            content="",
            tool_calls=[
                LLMToolCall(
                    id=f"call_loop_{idx}",
                    name="get_stock",
                    arguments="{}",
                )
            ],
        )
        for idx in range(MAX_TOOL_ITERATIONS - 2)
    ]
    mock_llm.generate_text.return_value = "Ringkasan stok berhasil diambil."

    agent = AgentService(llm_client=mock_llm)
    res = agent.run_agent(
        "cek laba rugi dan stok",
        session=seeded_db_session,
        today=date(2026, 11, 13),
    )

    assert "error" in res.tool_outputs[0]["output"]
    assert "tidak dikenal" in res.tool_outputs[1]["output"]["error"]
    assert "YYYY-MM" in res.tool_outputs[2]["output"]["error"]
    assert res.answer == "Ringkasan stok berhasil diambil."
    assert mock_llm.generate_text.call_count == 1


def test_strip_markdown_markers_and_agent_reply_sanitization(seeded_db_session):
    """Verify agent replies strip **, __, backticks, and # heading markers."""
    raw = (
        "### Ringkasan Laba Rugi\n"
        "**Omzet penjualan:** `Rp53.000`\n"
        "__Laba kotor:__ ```\nRp9.000\n```"
    )
    cleaned = strip_markdown_markers(raw)
    assert "**" not in cleaned
    assert "__" not in cleaned
    assert "`" not in cleaned
    assert not cleaned.startswith("#")
    assert "Omzet penjualan: Rp53.000" in cleaned
    assert "Laba kotor: Rp9.000" in cleaned

    mock_llm = MagicMock()
    mock_llm.generate_with_tools.return_value = LLMToolResponse(
        content="## Stok Saat Ini\n**Pelet Jitu:** __18 bungkus__ (`aman`)",
        tool_calls=[],
    )
    agent = AgentService(llm_client=mock_llm)
    reply = agent.answer_question("stok pelet jitu", session=seeded_db_session)
    assert reply == "Stok Saat Ini\nPelet Jitu: 18 bungkus (aman)"

    # Test juga jalur summary saat MAX_TOOL_ITERATIONS tercapai
    loop_llm = MagicMock()
    loop_llm.generate_with_tools.return_value = LLMToolResponse(
        content="",
        tool_calls=[LLMToolCall(id="c1", name="get_stock", arguments="{}")],
    )
    loop_llm.generate_text.return_value = "**Ringkasan:** `Rp19.000`"
    loop_agent = AgentService(llm_client=loop_llm)
    fallback_reply = loop_agent.answer_question("ringkasan", session=seeded_db_session)
    assert fallback_reply == "Ringkasan: Rp19.000"
