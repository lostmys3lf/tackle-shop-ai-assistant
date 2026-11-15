"""Read-only question-answering agent service using store data tools."""

# pylint: disable=too-few-public-methods

from dataclasses import dataclass, field
from datetime import date, timedelta
import json
from pathlib import Path
import re
from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure.db.connection import get_session
from app.infrastructure.db.repositories import ReportRepository, get_jakarta_today
from app.infrastructure.llm.client import (
    LLMClient,
    LLMRequest,
    LLMToolCall,
    get_llm_client,
)
from app.services.formatting import format_rupiah

DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "prompts" / "agent_v1.txt"
)
MAX_TOOL_ITERATIONS = 5


def strip_markdown_markers(text: str) -> str:
    """Remove Markdown formatting markers so Telegram plain-text replies render cleanly."""
    cleaned = re.sub(r"```[a-zA-Z0-9_-]*\n?", "", text)
    cleaned = cleaned.replace("`", "")
    cleaned = cleaned.replace("**", "").replace("__", "")
    cleaned = re.sub(r"(?m)^[ \t]*#+\s*", "", cleaned)
    cleaned = re.sub(r"(?m)[ \t]+#+[ \t]*$", "", cleaned)
    return cleaned.strip()


AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_stock",
            "description": (
                "Cek sisa stok, satuan, stok minimal, dan harga jual barang di toko. "
                "Gunakan tanpa product_query untuk melihat seluruh produk."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_query": {
                        "type": "string",
                        "description": "Kata kunci nama atau kategori barang (opsional).",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sales_summary",
            "description": (
                "Ambil total nilai penjualan, jumlah transaksi, dan total barang terjual "
                "dari transaksi penjualan terkonfirmasi pada rentang tanggal inklusif."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {
                        "type": "string",
                        "description": "Tanggal awal rentang (YYYY-MM-DD).",
                    },
                    "end": {
                        "type": "string",
                        "description": "Tanggal akhir rentang (YYYY-MM-DD).",
                    },
                },
                "required": ["start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "top_products",
            "description": (
                "Ambil daftar produk paling laris berdasarkan kuantitas terjual "
                "pada rentang tanggal inklusif."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {
                        "type": "string",
                        "description": "Tanggal awal rentang (YYYY-MM-DD).",
                    },
                    "end": {
                        "type": "string",
                        "description": "Tanggal akhir rentang (YYYY-MM-DD).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Jumlah maksimal produk terlaris yang ditampilkan.",
                    },
                },
                "required": ["start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "profit_loss",
            "description": (
                "Ambil laporan laba rugi sederhana bulanan (penjualan, HPP, laba kotor) "
                "berdasarkan jurnal buku besar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "month": {
                        "type": "string",
                        "description": "Bulan laporan dalam format YYYY-MM.",
                    }
                },
                "required": ["month"],
            },
        },
    },
]


@dataclass
class AgentRunResult:
    """Detailed outcome of an agent run for callers, tests, and evals."""

    answer: str
    tool_calls_made: list[str] = field(default_factory=list)
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)


def _parse_iso_date(raw_value: Any, fallback: date) -> date:
    """Parse YYYY-MM-DD date string or raise ValueError if non-empty and invalid."""
    if raw_value is None or str(raw_value).strip() == "":
        return fallback
    try:
        return date.fromisoformat(str(raw_value).strip())
    except ValueError as exc:
        raise ValueError(
            f"Format tanggal tidak valid '{raw_value}'. Gunakan YYYY-MM-DD."
        ) from exc


def _run_get_stock(repo: Any, args: dict[str, Any], _today: date) -> dict[str, Any]:
    """Execute get_stock read-only query tool."""
    query = args.get("product_query")
    products = repo.get_stock(str(query) if query is not None else None)
    return {
        "product_query": query,
        "count": len(products),
        "products": [
            {
                "id": prod.id,
                "name": prod.name,
                "category": prod.category,
                "unit": prod.unit,
                "stock_qty": prod.stock_qty,
                "min_stock": prod.min_stock,
                "is_low_stock": prod.stock_qty <= prod.min_stock,
                "sell_price": str(prod.sell_price),
                "sell_price_formatted": format_rupiah(prod.sell_price),
            }
            for prod in products
        ],
    }


def _run_sales_summary(repo: Any, args: dict[str, Any], today: date) -> dict[str, Any]:
    """Execute sales_summary read-only query tool."""
    start_date = _parse_iso_date(args.get("start"), today)
    end_date = _parse_iso_date(args.get("end"), today)
    summary = repo.get_sales_summary(start_date, end_date)
    return {
        "start": summary.start_date.isoformat(),
        "end": summary.end_date.isoformat(),
        "transaction_count": summary.transaction_count,
        "total_items_sold": summary.total_items_sold,
        "total_sales": str(summary.total_sales),
        "total_sales_formatted": format_rupiah(summary.total_sales),
    }


def _run_top_products(repo: Any, args: dict[str, Any], today: date) -> dict[str, Any]:
    """Execute top_products read-only query tool."""
    month_start = today.replace(day=1)
    start_date = _parse_iso_date(args.get("start"), month_start)
    end_date = _parse_iso_date(args.get("end"), today)
    raw_limit = args.get("limit", 5)
    try:
        limit = int(raw_limit) if raw_limit is not None else 5
    except (TypeError, ValueError) as exc:
        raise ValueError("Parameter 'limit' harus berupa angka bulat.") from exc

    ranked = repo.get_top_products(start_date, end_date, limit=limit)
    return {
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "count": len(ranked),
        "top_products": [
            {
                "rank": idx,
                "product_id": item.product_id,
                "product_name": item.product_name,
                "unit": item.unit,
                "total_qty": item.total_qty,
                "total_revenue": str(item.total_revenue),
                "total_revenue_formatted": format_rupiah(item.total_revenue),
            }
            for idx, item in enumerate(ranked, start=1)
        ],
    }


def _run_profit_loss(repo: Any, args: dict[str, Any], today: date) -> dict[str, Any]:
    """Execute profit_loss read-only query tool."""
    month_str = str(args.get("month") or today.strftime("%Y-%m")).strip()
    pnl = repo.get_profit_loss(month_str)
    return {
        "month": pnl.month,
        "total_sales": str(pnl.total_sales),
        "total_sales_formatted": format_rupiah(pnl.total_sales),
        "total_cogs": str(pnl.total_cogs),
        "total_cogs_formatted": format_rupiah(pnl.total_cogs),
        "gross_profit": str(pnl.gross_profit),
        "gross_profit_formatted": format_rupiah(pnl.gross_profit),
    }


_TOOL_HANDLERS = {
    "get_stock": _run_get_stock,
    "sales_summary": _run_sales_summary,
    "top_products": _run_top_products,
    "profit_loss": _run_profit_loss,
}


def _execute_tool_call(
    tool_call: LLMToolCall,
    repo: Any,
    today: date,
) -> dict[str, Any]:
    """Parse tool arguments safely and run corresponding read-only repository query."""
    handler = _TOOL_HANDLERS.get(tool_call.name)
    if handler is None:
        return {"error": f"Tool '{tool_call.name}' tidak dikenal."}

    try:
        parsed = json.loads(tool_call.arguments or "{}")
    except json.JSONDecodeError as exc:
        return {"error": f"Argumen JSON tidak valid: {exc}"}

    if not isinstance(parsed, dict):
        return {"error": "Argumen tool harus berupa objek JSON."}

    try:
        return handler(repo, parsed, today)
    except ValueError as exc:
        return {"error": str(exc)}


class AgentService:
    """Service running a tool-calling loop to answer owner questions from store data."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        prompt_path: Path | None = None,
        report_repo: Any | None = None,
    ) -> None:
        """Initialize agent with LLM client, prompt template, and optional report repo."""
        self.client = llm_client or get_llm_client()
        self.prompt_path = prompt_path or DEFAULT_PROMPT_PATH
        self._prompt_template = self._load_prompt()
        self.report_repo = report_repo

    def _load_prompt(self) -> str:
        """Load agent system prompt template from disk."""
        if not self.prompt_path.exists():
            raise FileNotFoundError(
                f"Agent prompt file not found at: {self.prompt_path}"
            )
        return self.prompt_path.read_text(encoding="utf-8").strip()

    def _build_system_prompt(self, today: date) -> str:
        """Inject Indonesian calendar reference dates into agent system prompt."""
        yesterday = today - timedelta(days=1)
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)
        replacements = {
            "{today_date}": today.isoformat(),
            "{yesterday_date}": yesterday.isoformat(),
            "{week_start_date}": week_start.isoformat(),
            "{month_start_date}": month_start.isoformat(),
            "{current_month}": today.strftime("%Y-%m"),
        }
        prompt = self._prompt_template
        for key, value in replacements.items():
            prompt = prompt.replace(key, value)
        return prompt

    def _run_loop(self, question: str, repo: Any, today: date) -> AgentRunResult:
        """Run iterative LLM tool-calling loop until final answer is produced."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt(today)},
            {"role": "user", "content": question},
        ]
        tool_calls_made: list[str] = []
        tool_outputs: list[dict[str, Any]] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            req = LLMRequest(
                messages=messages,
                tools=AGENT_TOOLS,
                tool_choice="auto",
                prompt_version="agent_v1",
                name="store_question_agent",
                temperature=0.0,
                max_tokens=1500,
            )
            response = self.client.generate_with_tools(req)

            if not response.tool_calls:
                answer = strip_markdown_markers(response.content.strip())
                if not answer:
                    answer = (
                        "Maaf, saya belum bisa menyusun jawaban dari pertanyaan tersebut. "
                        "Mohon coba tulis pertanyaan dengan lebih spesifik."
                    )
                return AgentRunResult(
                    answer=answer,
                    tool_calls_made=tool_calls_made,
                    tool_outputs=tool_outputs,
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": response.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": call.arguments,
                            },
                        }
                        for call in response.tool_calls
                    ],
                }
            )

            for call in response.tool_calls:
                output = _execute_tool_call(call, repo, today)
                tool_calls_made.append(call.name)
                tool_outputs.append({"tool": call.name, "output": output})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(output, ensure_ascii=False),
                    }
                )

        # Jika batas iterasi tool tercapai, minta ringkasan akhir tanpa memanggil tool lagi
        final_req = LLMRequest(
            messages=messages,
            prompt_version="agent_v1",
            name="store_question_agent_summary",
            temperature=0.0,
            max_tokens=1500,
        )
        final_text = strip_markdown_markers(
            self.client.generate_text(final_req).strip()
        )
        if not final_text:
            final_text = (
                "Maaf, permintaan membutuhkan terlalu banyak langkah. "
                "Silakan persempit pertanyaan."
            )
        return AgentRunResult(
            answer=final_text,
            tool_calls_made=tool_calls_made,
            tool_outputs=tool_outputs,
        )

    def run_agent(
        self,
        question: str,
        session: Session | None = None,
        today: date | None = None,
    ) -> AgentRunResult:
        """Execute question answering with injected repo, provided session, or new session."""
        ref_date = today or get_jakarta_today()

        if self.report_repo is not None:
            return self._run_loop(question, self.report_repo, ref_date)

        if session is not None:
            return self._run_loop(question, ReportRepository(session), ref_date)

        with get_session() as db_session:
            return self._run_loop(question, ReportRepository(db_session), ref_date)

    def answer_question(
        self,
        question: str,
        session: Session | None = None,
        today: date | None = None,
    ) -> str:
        """Answer store question using read-only data tools and return final reply text."""
        return self.run_agent(question, session=session, today=today).answer
