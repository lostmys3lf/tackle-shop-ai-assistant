"""Message processing service orchestrating routing, extraction, and reply templates."""

# pylint: disable=too-few-public-methods

from decimal import Decimal
from typing import Any

from app.domain.models import DomainProduct, TransactionSource, TransactionType
from app.infrastructure.db.connection import get_session
from app.infrastructure.db.repositories import (
    MessageRepository,
    ProductRepository,
    TransactionRepository,
)
from app.infrastructure.llm.extractor import (
    ExtractedItem,
    ExtractedTransaction,
    TransactionExtractor,
)
from app.infrastructure.llm.router import (
    BaseMessageRouter,
    MessageIntent,
    get_message_router,
)


def _format_currency(amount: Decimal) -> str:
    """Format decimal amount into Indonesian Rupiah format."""
    return f"Rp {amount:,.2f}"


def _build_confirmation_text(
    tx_type: TransactionType,
    items: list[tuple[ExtractedItem, DomainProduct]],
    warnings: list[str],
) -> str:
    """Build confirmation message text purely from Python template."""
    title = (
        "Catatan Penjualan:"
        if tx_type == TransactionType.SALE
        else "Catatan Pembelian:"
    )
    lines = [title]
    total_amount = Decimal("0.00")

    for item, product in items:
        unit_price = item.unit_price or product.sell_price
        subtotal = Decimal(item.qty) * unit_price
        total_amount += subtotal
        lines.append(
            f"- {product.name}: {item.qty} {product.unit} x "
            f"{_format_currency(unit_price)} = {_format_currency(subtotal)}"
        )

    lines.append(f"Total: {_format_currency(total_amount)}")

    if warnings:
        lines.append("")
        for warn in warnings:
            lines.append(f"⚠️ {warn}")

    lines.append("")
    lines.append('Ketik "Benar" untuk konfirmasi atau "Salah" untuk batalkan.')
    return "\n".join(lines)


class MessageService:
    """Service handling incoming messages, intent classification, and transaction drafts."""

    def __init__(
        self,
        router: BaseMessageRouter | None = None,
        extractor: TransactionExtractor | None = None,
    ) -> None:
        """Initialize message service with router and extractor."""
        self.router = router or get_message_router()
        self.extractor = extractor or TransactionExtractor()

    def process_message(  # pylint: disable=too-many-locals
        self,
        channel: str,
        chat_id: str,
        text: str | None,
        image_base64: str | None = None,  # pylint: disable=unused-argument
    ) -> dict[str, Any]:
        """Process incoming chat message and return response payload."""
        clean_text = (text or "").strip()
        if not clean_text:
            return {
                "intent": MessageIntent.OTHER.value,
                "reply_text": "Pesan kosong. Silakan ketik transaksi atau pertanyaan Anda.",
                "pending_transaction_id": None,
            }

        with get_session() as session:
            msg_repo = MessageRepository(session)
            prod_repo = ProductRepository(session)
            tx_repo = TransactionRepository(session)

            # 1. Simpan pesan masuk
            msg = msg_repo.create_message(
                channel=channel,
                chat_id=chat_id,
                text=clean_text,
            )

            # 2. Routing jenis pesan
            routing = self.router.route(clean_text)
            intent = routing.intent
            confidence = Decimal(str(round(routing.confidence, 4)))

            # 3. Handle pertanyaan dan percakapan umum
            if intent == MessageIntent.QUESTION:
                reply = (
                    "Pertanyaan Anda tercatat. Fitur tanya jawab data toko otomatis "
                    "akan aktif pada modul Agent (Task 10)."
                )
                msg_repo.update_reply(msg.id, reply, intent.value, confidence)
                return {
                    "intent": intent.value,
                    "reply_text": reply,
                    "pending_transaction_id": None,
                }

            if intent == MessageIntent.OTHER:
                reply = (
                    "Halo! Saya bot pencatatan Toko Pancing Papa. "
                    "Saya membantu mencatat penjualan, pembelian stok, dan laporan toko."
                )
                msg_repo.update_reply(msg.id, reply, intent.value, confidence)
                return {
                    "intent": intent.value,
                    "reply_text": reply,
                    "pending_transaction_id": None,
                }

            # 4. Ekstraksi transaksi (penjualan / pembelian)
            products = prod_repo.get_all()
            products_by_id = {p.id: p for p in products if p.id is not None}

            extracted: ExtractedTransaction = self.extractor.extract(
                clean_text, products
            )

            # Validasi barang tidak dikenali
            for item in extracted.items:
                if item.product_id is None or item.product_id not in products_by_id:
                    reply = (
                        f"Maaf, barang mana yang dimaksud untuk '{item.product_name_raw}'? "
                        "Produk tidak ditemukan di daftar produk toko."
                    )
                    msg_repo.update_reply(msg.id, reply, intent.value, confidence)
                    return {
                        "intent": intent.value,
                        "reply_text": reply,
                        "pending_transaction_id": None,
                    }

            # Validasi harga beli jika pembelian stok
            if extracted.type == TransactionType.PURCHASE:
                for item in extracted.items:
                    if item.unit_price is None or item.unit_price <= Decimal("0.00"):
                        reply = f"Berapa harga beli atau kulakan untuk '{item.product_name_raw}'?"
                        msg_repo.update_reply(msg.id, reply, intent.value, confidence)
                        return {
                            "intent": intent.value,
                            "reply_text": reply,
                            "pending_transaction_id": None,
                        }

            # 5. Persiapkan draft transaksi pending
            items_payload: list[dict[str, Any]] = []
            matched_items: list[tuple[ExtractedItem, DomainProduct]] = []
            warnings: list[str] = []

            for item in extracted.items:
                product = products_by_id[item.product_id]  # type: ignore[index]
                unit_price = (
                    item.unit_price
                    if item.unit_price is not None and item.unit_price > Decimal("0.00")
                    else product.sell_price
                )

                # Berikan peringatan lebih awal jika penjualan melebihi stok
                if (
                    extracted.type == TransactionType.SALE
                    and item.qty > product.stock_qty
                ):
                    warnings.append(
                        f"Peringatan: Stok '{product.name}' saat ini tersisa {product.stock_qty} "
                        f"{product.unit}, sedangkan diminta {item.qty} {product.unit}."
                    )

                items_payload.append(
                    {
                        "product_id": product.id,
                        "qty": item.qty,
                        "unit_price": unit_price,
                    }
                )
                matched_items.append((item, product))

            # 6. Buat transaksi berstatus pending
            tx = tx_repo.create_pending_transaction(
                tx_type=extracted.type.value,
                source=TransactionSource.CHAT.value,
                raw_text=clean_text,
                items=items_payload,
            )

            # 7. Susun teks konfirmasi template
            reply = _build_confirmation_text(extracted.type, matched_items, warnings)
            msg_repo.update_reply(msg.id, reply, intent.value, confidence)

            return {
                "intent": intent.value,
                "reply_text": reply,
                "pending_transaction_id": tx.id,
            }
