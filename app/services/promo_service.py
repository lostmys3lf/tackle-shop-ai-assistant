"""Service layer for slow-moving inventory promo captions and deterministic bundling."""

# pylint: disable=too-few-public-methods

import json
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.domain.models import BundleSuggestionData, ProductMovementStats
from app.infrastructure.db.connection import get_session
from app.infrastructure.db.repositories import PromoRepository
from app.infrastructure.llm.client import LLMClient, LLMRequest
from app.services.formatting import format_rupiah

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "promo_v1.txt"
PROMPT_VERSION = "promo_v1"
DEFAULT_WINDOW_DAYS = 30
DEFAULT_SLOW_LIMIT = 3
BUNDLE_DISCOUNT_STEP = Decimal("500")
BUNDLE_DISCOUNT_RATE = Decimal("0.05")
MONEY_QUANT = Decimal("0.01")


@dataclass(frozen=True)
class SlowMovingPromoOptions:
    """Parameters controlling slow-moving promo calculation window and result limit."""

    window_days: int = DEFAULT_WINDOW_DAYS
    limit: int = DEFAULT_SLOW_LIMIT
    reference_date: date | None = None


class PromoLLMOutput(BaseModel):
    """Validated JSON response returned by LLM for promo captions and bundling advice."""

    caption: str = Field(min_length=1)
    bundling_advice: str = Field(min_length=1)


def _validate_options(options: SlowMovingPromoOptions) -> SlowMovingPromoOptions:
    """Validate slow-moving promo options against domain bounds."""
    if options.window_days < 1 or options.window_days > 365:
        raise ValueError("Rentang hari (window_days) harus antara 1 sampai 365 hari.")
    if options.limit < 1 or options.limit > 10:
        raise ValueError("Batas produk (limit) harus antara 1 sampai 10.")
    return options


def _select_slow_moving_items(
    stats: list[ProductMovementStats],
    limit: int,
) -> list[ProductMovementStats]:
    """Select slow-moving candidates giving priority to zero-sale inventory."""
    if not stats:
        return []
    zero_movers = [item for item in stats if item.sold_qty_window == 0]
    if zero_movers:
        return zero_movers[:limit]
    max_sold = max(item.sold_qty_window for item in stats)
    slower_items = [
        item
        for item in stats
        if item.sold_qty_window < max_sold or item.sold_qty_window <= 2
    ]
    if slower_items:
        return slower_items[:limit]
    return stats[:limit]


def _select_bundle_partner(
    slow_item: ProductMovementStats,
    all_items: list[ProductMovementStats],
) -> ProductMovementStats | None:
    """Pick a fast-moving or complementary-category product as a bundling partner."""
    candidates = [item for item in all_items if item.product.id != slow_item.product.id]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            -item.sold_qty_window,
            0 if item.product.category != slow_item.product.category else 1,
            item.product.id or 0,
        )
    )
    return candidates[0]


def calculate_bundle_offer(
    slow_item: ProductMovementStats,
    partner_item: ProductMovementStats,
) -> BundleSuggestionData:
    """Compute deterministic bundle pricing in Decimal without ever selling below COGS."""
    slow_product = slow_item.product
    partner_product = partner_item.product
    normal_total = (slow_product.sell_price + partner_product.sell_price).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )
    combined_cost = (slow_product.avg_cost + partner_product.avg_cost).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )
    max_safe_discount = max(Decimal("0.00"), normal_total - combined_cost)

    # Bulatkan diskon promo target 5% ke kelipatan Rp500, tetap aman di atas total HPP
    target_discount = (
        (normal_total * BUNDLE_DISCOUNT_RATE) / BUNDLE_DISCOUNT_STEP
    ).to_integral_value(rounding=ROUND_HALF_UP) * BUNDLE_DISCOUNT_STEP
    max_stepped_discount = (max_safe_discount / BUNDLE_DISCOUNT_STEP).to_integral_value(
        rounding=ROUND_FLOOR
    ) * BUNDLE_DISCOUNT_STEP

    discount_amount = max(
        min(target_discount, max_stepped_discount), Decimal("0.00")
    ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

    bundle_price = (normal_total - discount_amount).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )
    discount_percent = (
        int(
            ((discount_amount * Decimal("100")) / normal_total).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        if normal_total > Decimal("0.00")
        else 0
    )

    return BundleSuggestionData(
        slow_product=slow_product,
        partner_product=partner_product,
        normal_total_price=normal_total,
        bundle_price=bundle_price,
        discount_amount=discount_amount,
        discount_percent=discount_percent,
    )


def _build_bundle_suggestions(
    slow_items: list[ProductMovementStats],
    all_stats: list[ProductMovementStats],
) -> list[BundleSuggestionData]:
    """Generate unique deterministic bundle suggestions for selected slow items."""
    suggestions: list[BundleSuggestionData] = []
    seen_pairs: set[tuple[int, int]] = set()
    for slow_item in slow_items:
        partner = _select_bundle_partner(slow_item, all_stats)
        if (
            partner is None
            or slow_item.product.id is None
            or partner.product.id is None
        ):
            continue
        pair_key = (slow_item.product.id, partner.product.id)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        suggestions.append(calculate_bundle_offer(slow_item, partner))
    return suggestions


def _serialize_slow_item(item: ProductMovementStats) -> dict[str, Any]:
    """Convert ProductMovementStats into API and prompt dictionary representation."""
    product = item.product
    return {
        "product_id": product.id or 0,
        "product_name": product.name,
        "category": product.category,
        "unit": product.unit,
        "stock_qty": product.stock_qty,
        "sell_price": product.sell_price,
        "sell_price_formatted": format_rupiah(product.sell_price),
        "sold_qty_window": item.sold_qty_window,
        "last_sold_date": (
            item.last_sold_date.isoformat() if item.last_sold_date is not None else None
        ),
        "days_since_last_sale": item.days_since_last_sale,
        "stock_value": item.stock_value,
        "stock_value_formatted": format_rupiah(item.stock_value),
    }


def _serialize_bundle_item(bundle: BundleSuggestionData) -> dict[str, Any]:
    """Convert BundleSuggestionData into API and prompt dictionary representation."""
    return {
        "slow_product_id": bundle.slow_product.id or 0,
        "slow_product_name": bundle.slow_product.name,
        "partner_product_id": bundle.partner_product.id or 0,
        "partner_product_name": bundle.partner_product.name,
        "normal_total_price": bundle.normal_total_price,
        "normal_total_price_formatted": format_rupiah(bundle.normal_total_price),
        "bundle_price": bundle.bundle_price,
        "bundle_price_formatted": format_rupiah(bundle.bundle_price),
        "discount_amount": bundle.discount_amount,
        "discount_amount_formatted": format_rupiah(bundle.discount_amount),
        "discount_percent": bundle.discount_percent,
    }


def _build_promo_prompt(
    window_days: int,
    slow_payload: list[dict[str, Any]],
    bundle_payload: list[dict[str, Any]],
) -> str:
    """Populate versioned promo prompt template using strictly precomputed numbers."""
    template = PROMPT_PATH.read_text(encoding="utf-8")
    # Hanya kirim angka terhitung dalam string yang rapi agar LLM tidak menghitung ulang
    prompt_slow = [
        {
            "product_name": row["product_name"],
            "stock_text": f"{row['stock_qty']} {row['unit']}",
            "sold_qty_window": row["sold_qty_window"],
            "days_since_last_sale": row["days_since_last_sale"],
            "sell_price_formatted": row["sell_price_formatted"],
        }
        for row in slow_payload
    ]
    prompt_bundles = [
        {
            "slow_product_name": row["slow_product_name"],
            "partner_product_name": row["partner_product_name"],
            "normal_total_price_formatted": row["normal_total_price_formatted"],
            "bundle_price_formatted": row["bundle_price_formatted"],
            "discount_amount_formatted": row["discount_amount_formatted"],
            "discount_percent": row["discount_percent"],
        }
        for row in bundle_payload
    ]
    return (
        template.replace("{window_days}", str(window_days))
        .replace(
            "{slow_moving_data}",
            json.dumps(prompt_slow, ensure_ascii=False, indent=2),
        )
        .replace(
            "{bundling_data}",
            json.dumps(prompt_bundles, ensure_ascii=False, indent=2),
        )
    )


def _build_summary_text(
    window_days: int,
    slow_payload: list[dict[str, Any]],
    bundle_payload: list[dict[str, Any]],
    llm_output: PromoLLMOutput,
) -> str:
    """Format full Telegram-ready promo recommendation combining data and LLM text."""
    lines = [f"Rekomendasi Promo Mingguan ({window_days} hari terakhir):"]
    for item in slow_payload:
        idle_text = (
            "belum pernah terjual"
            if item["days_since_last_sale"] is None
            else f"{item['days_since_last_sale']} hari tidak laku"
        )
        lines.append(
            f"- {item['product_name']}: terjual {item['sold_qty_window']} {item['unit']} "
            f"| stok {item['stock_qty']} {item['unit']} | {idle_text} "
            f"| harga {item['sell_price_formatted']}"
        )

    if bundle_payload:
        lines.append("\nPaket Bundling (Angka Data):")
        for bundle in bundle_payload:
            promo_note = (
                f"hemat {bundle['discount_amount_formatted']} ({bundle['discount_percent']}%)"
                if bundle["discount_amount"] > Decimal("0.00")
                else "harga paket normal"
            )
            lines.append(
                f"- {bundle['slow_product_name']} + {bundle['partner_product_name']}: "
                f"{bundle['bundle_price_formatted']} "
                f"(normal {bundle['normal_total_price_formatted']}, {promo_note})"
            )

    lines.append(f"\nSaran Bundling:\n{llm_output.bundling_advice.strip()}")
    lines.append(f"\nCaption Siap Pakai:\n{llm_output.caption.strip()}")
    return "\n".join(lines)


class PromoService:
    """Service orchestrating slow-moving inventory analysis and LLM promo phrasing."""

    def __init__(
        self,
        promo_repo: Any | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        """Initialize promo service with optional injected repository and LLM client."""
        self.promo_repo = promo_repo
        self.llm_client = llm_client

    def _fetch_movement_stats(
        self,
        options: SlowMovingPromoOptions,
        session: Session | None,
    ) -> list[ProductMovementStats]:
        """Load active product velocity data from injected repo or DB session."""
        if self.promo_repo is not None:
            return self.promo_repo.get_product_movement_stats(
                window_days=options.window_days,
                reference_date=options.reference_date,
            )
        if session is not None:
            return PromoRepository(session).get_product_movement_stats(
                window_days=options.window_days,
                reference_date=options.reference_date,
            )
        with get_session() as db_session:
            return PromoRepository(db_session).get_product_movement_stats(
                window_days=options.window_days,
                reference_date=options.reference_date,
            )

    def generate_slow_moving_promo(
        self,
        options: SlowMovingPromoOptions | None = None,
        session: Session | None = None,
    ) -> dict[str, Any]:
        """Identify slow-moving inventory and generate verified promo copy and bundles."""
        cfg = _validate_options(options or SlowMovingPromoOptions())
        all_stats = self._fetch_movement_stats(cfg, session)
        slow_items = _select_slow_moving_items(all_stats, cfg.limit)

        if not slow_items:
            return {
                "has_slow_moving": False,
                "window_days": cfg.window_days,
                "slow_moving_items": [],
                "bundle_suggestions": [],
                "caption": "",
                "bundling_advice": "",
                "summary_text": (
                    "Saat ini belum ada produk berstok aktif yang perlu dipromosikan."
                ),
            }

        slow_payload = [_serialize_slow_item(item) for item in slow_items]
        bundle_payload = [
            _serialize_bundle_item(bundle)
            for bundle in _build_bundle_suggestions(slow_items, all_stats)
        ]

        prompt_text = _build_promo_prompt(cfg.window_days, slow_payload, bundle_payload)
        client = self.llm_client or LLMClient()
        llm_output = client.generate_structured(
            LLMRequest(
                messages=[{"role": "user", "content": prompt_text}],
                prompt_version=PROMPT_VERSION,
                name="slow_moving_promo",
                temperature=0.4,
                max_tokens=1500,
            ),
            PromoLLMOutput,
        )

        return {
            "has_slow_moving": True,
            "window_days": cfg.window_days,
            "slow_moving_items": slow_payload,
            "bundle_suggestions": bundle_payload,
            "caption": llm_output.caption.strip(),
            "bundling_advice": llm_output.bundling_advice.strip(),
            "summary_text": _build_summary_text(
                cfg.window_days, slow_payload, bundle_payload, llm_output
            ),
        }
