"""Report generation service reusing read-only repository aggregations."""

# pylint: disable=too-few-public-methods

from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.domain.models import (
    DomainProduct,
    ProfitLossData,
    SalesSummaryData,
    TopProductData,
)
from app.infrastructure.db.connection import get_session
from app.infrastructure.db.repositories import ReportRepository, get_jakarta_today
from app.services.formatting import format_rupiah

TOP_PRODUCTS_LIMIT = 5


def _build_low_stock_summary(items: list[DomainProduct]) -> str:
    """Format low-stock alert text purely via Python template."""
    if not items:
        return "Semua stok barang saat ini masih aman di atas batas minimal."

    lines = [f"Peringatan Stok Menipis ({len(items)} produk):"]
    for item in items:
        lines.append(
            f"- {item.name}: sisa {item.stock_qty} {item.unit} "
            f"(minimal {item.min_stock} {item.unit})"
        )
    return "\n".join(lines)


def _build_sales_summary_text(
    summary: SalesSummaryData,
    top_products: list[TopProductData],
) -> str:
    """Format sales and best-selling products report text purely via Python template."""
    start_str = summary.start_date.isoformat()
    end_str = summary.end_date.isoformat()
    lines = [
        f"Laporan Penjualan ({start_str} s.d. {end_str}):",
        f"- Total Penjualan: {format_rupiah(summary.total_sales)}",
        f"- Jumlah Transaksi: {summary.transaction_count}",
        f"- Total Barang Terjual: {summary.total_items_sold}",
    ]
    if top_products:
        lines.append("")
        lines.append("Barang Terlaris:")
        for idx, item in enumerate(top_products, start=1):
            lines.append(
                f"{idx}. {item.product_name}: {item.total_qty} {item.unit} "
                f"({format_rupiah(item.total_revenue)})"
            )
    else:
        lines.append("")
        lines.append("Belum ada penjualan terkonfirmasi pada periode ini.")
    return "\n".join(lines)


def _build_profit_loss_text(pnl: ProfitLossData) -> str:
    """Format monthly profit-and-loss text purely via Python template."""
    return "\n".join(
        [
            f"Laporan Laba Rugi Sederhana ({pnl.month}):",
            f"- Penjualan: {format_rupiah(pnl.total_sales)}",
            f"- HPP: {format_rupiah(pnl.total_cogs)}",
            f"- Laba Kotor: {format_rupiah(pnl.gross_profit)}",
        ]
    )


def resolve_sales_date_range(
    start: date | None,
    end: date | None,
    today: date | None = None,
) -> tuple[date, date]:
    """Resolve optional start/end dates into an inclusive [start, end] range."""
    ref_today = today or get_jakarta_today()
    resolved_end = end or ref_today
    resolved_start = start or (resolved_end - timedelta(days=6))
    if resolved_end < resolved_start:
        raise ValueError("Tanggal akhir (end) tidak boleh lebih awal dari start.")
    return resolved_start, resolved_end


class ReportService:
    """Service layer generating structured store reports and plain-text summaries."""

    def __init__(self, report_repo: Any | None = None) -> None:
        """Initialize report service with optional injected repository for testing."""
        self.report_repo = report_repo

    def _run_with_repo(self, action: Any, session: Session | None = None) -> Any:
        """Execute repository query using injected repo, provided session, or new session."""
        if self.report_repo is not None:
            return action(self.report_repo)
        if session is not None:
            return action(ReportRepository(session))
        with get_session() as db_session:
            return action(ReportRepository(db_session))

    def get_low_stock_report(self, session: Session | None = None) -> dict[str, Any]:
        """Return low-stock products and formatted alert message."""
        items: list[DomainProduct] = self._run_with_repo(
            lambda repo: repo.get_low_stock(),
            session=session,
        )
        return {
            "count": len(items),
            "has_low_stock": bool(items),
            "items": [
                {
                    "product_id": item.id,
                    "name": item.name,
                    "category": item.category,
                    "unit": item.unit,
                    "stock_qty": item.stock_qty,
                    "min_stock": item.min_stock,
                }
                for item in items
            ],
            "summary_text": _build_low_stock_summary(items),
        }

    def get_sales_report(
        self,
        start: date | None = None,
        end: date | None = None,
        session: Session | None = None,
    ) -> dict[str, Any]:
        """Return aggregated sales total, transaction count, and top products for range."""
        start_date, end_date = resolve_sales_date_range(start, end)

        def _query(repo: Any) -> tuple[SalesSummaryData, list[TopProductData]]:
            summary_data = repo.get_sales_summary(start_date, end_date)
            top_data = repo.get_top_products(
                start_date, end_date, limit=TOP_PRODUCTS_LIMIT
            )
            return summary_data, top_data

        summary, top_products = self._run_with_repo(_query, session=session)
        return {
            "start_date": summary.start_date,
            "end_date": summary.end_date,
            "total_sales": summary.total_sales,
            "total_sales_formatted": format_rupiah(summary.total_sales),
            "transaction_count": summary.transaction_count,
            "total_items_sold": summary.total_items_sold,
            "top_products": [
                {
                    "product_id": item.product_id,
                    "product_name": item.product_name,
                    "category": item.category,
                    "unit": item.unit,
                    "total_qty": item.total_qty,
                    "total_revenue": item.total_revenue,
                    "total_revenue_formatted": format_rupiah(item.total_revenue),
                }
                for item in top_products
            ],
            "summary_text": _build_sales_summary_text(summary, top_products),
        }

    def get_profit_loss_report(
        self,
        month: str | None = None,
        session: Session | None = None,
    ) -> dict[str, Any]:
        """Return monthly sales revenue, COGS, and gross profit report."""
        target_month = (month or get_jakarta_today().strftime("%Y-%m")).strip()
        pnl: ProfitLossData = self._run_with_repo(
            lambda repo: repo.get_profit_loss(target_month),
            session=session,
        )
        return {
            "month": pnl.month,
            "total_sales": pnl.total_sales,
            "total_sales_formatted": format_rupiah(pnl.total_sales),
            "total_cogs": pnl.total_cogs,
            "total_cogs_formatted": format_rupiah(pnl.total_cogs),
            "gross_profit": pnl.gross_profit,
            "gross_profit_formatted": format_rupiah(pnl.gross_profit),
            "summary_text": _build_profit_loss_text(pnl),
        }
