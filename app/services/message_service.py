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
    ExtractedTransactionType,
    TransactionExtractor,
)
from app.infrastructure.llm.router import (
    BaseMessageRouter,
    MessageIntent,
    get_message_router,
)
from app.services.agent_service import AgentService
from app.services.formatting import format_rupiah

WELCOME_MESSAGE = (
    "Halo, Pak Jon! Selamat datang di Asisten Toko Pancing Bapak.\n"
    "Saya siap membantu mencatat transaksi, memantau stok, dan merangkum laporan toko.\n"
    "Aturan: Satu chat hanya untuk satu jenis transaksi (penjualan atau pembelian saja).\n\n"
    "Contoh chat sesuai kategori:\n\n"
    "Peralatan (Senar Litecate, Senar Shino):\n"
    "• laku senar litecate 1 gulung\n"
    "• beli senar litecate 5 gulung harga 29000\n\n"
    "Bahan Umpan (Pelet Jitu, Umpan Pak Ikin, Umpan Tepung Bogor):\n"
    "• laku pelet jitu 2 bungkus\n"
    "• beli pelet jitu 10 bungkus harga 2500\n\n"
    "Cek Stok & Laporan:\n"
    "• sisa stok pelet berapa?\n"
    "• penjualan hari ini berapa?\n"
    "• barang apa yang paling laris?\n\n"
    "Ketik /start kapan saja untuk melihat panduan ini lagi."
)

MIXED_TRANSACTION_REPLY = (
    "Satu chat hanya untuk satu jenis transaksi (penjualan saja atau pembelian saja).\n"
    "Mohon pisahkan catatan penjualan dan pembelian menjadi dua chat terpisah "
    "agar dapat diproses dan dicatat dengan benar."
)


def _is_welcome_command(text: str) -> bool:
    """Return True if message starts with /start or /help command."""
    first_token = text.strip().split(maxsplit=1)[0].lower()
    command = first_token.split("@", maxsplit=1)[0]
    return command in {"/start", "/help"}


def _format_currency(amount: Decimal) -> str:
    """Format decimal amount into Indonesian Rupiah without decimals (e.g. Rp3.000)."""
    return format_rupiah(amount)


def _build_confirmation_text(
    tx_type: TransactionType,
    items: list[tuple[ExtractedItem, DomainProduct]],
    warnings: list[str],
) -> str:
    """Build confirmation message text purely from Python template."""
    title = (
        "Konfirmasi PENJUALAN:"
        if tx_type == TransactionType.SALE
        else "Konfirmasi PEMBELIAN:"
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
    lines.append(
        'Tekan tombol "Benar" untuk konfirmasi atau "Salah" untuk membatalkan.'
    )
    return "\n".join(lines)


class MessageService:
    """Service handling incoming messages, intent classification, and transaction drafts."""

    def __init__(
        self,
        router: BaseMessageRouter | None = None,
        extractor: TransactionExtractor | None = None,
        agent_service: AgentService | None = None,
    ) -> None:
        """Initialize message service with router, extractor, and agent."""
        self.router = router or get_message_router()
        self.extractor = extractor or TransactionExtractor()
        self.agent_service = agent_service or AgentService()

    def process_message(
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

            # 1. Simpan pesan masuk
            msg = msg_repo.create_message(
                channel=channel,
                chat_id=chat_id,
                text=clean_text,
            )

            # Tangani /start dan /help sebagai panduan awal tanpa panggil LLM
            if _is_welcome_command(clean_text):
                msg_repo.update_reply(
                    msg.id,
                    WELCOME_MESSAGE,
                    MessageIntent.OTHER.value,
                    Decimal("1.0000"),
                )
                return {
                    "intent": MessageIntent.OTHER.value,
                    "reply_text": WELCOME_MESSAGE,
                    "pending_transaction_id": None,
                }

            # 2. Routing jenis pesan
            routing = self.router.route(clean_text)
            intent = routing.intent
            confidence = Decimal(str(round(routing.confidence, 4)))
            pending_tx_id: int | None = None

            # 3. Handle pertanyaan, percakapan umum, atau transaksi
            if intent == MessageIntent.QUESTION:
                reply = self.agent_service.answer_question(clean_text, session=session)
            elif intent == MessageIntent.OTHER:
                reply = (
                    "Halo! Saya bot pencatatan Toko Pancing Bapak. "
                    "Saya membantu mencatat penjualan, pembelian stok, dan laporan toko.\n"
                    "Ketik /start untuk melihat contoh chat yang bisa dicoba."
                )
            else:
                reply, pending_tx_id = self._process_transaction_intent(
                    clean_text=clean_text,
                    session=session,
                )

            msg_repo.update_reply(msg.id, reply, intent.value, confidence)
            return {
                "intent": intent.value,
                "reply_text": reply,
                "pending_transaction_id": pending_tx_id,
            }

    def _process_transaction_intent(  # pylint: disable=too-many-locals
        self,
        clean_text: str,
        session: Any,
    ) -> tuple[str, int | None]:
        """Extract transaction items, validate stock/prices, and create pending draft."""
        prod_repo = ProductRepository(session)
        tx_repo = TransactionRepository(session)
        products = prod_repo.get_all()
        products_by_id = {p.id: p for p in products if p.id is not None}

        extracted: ExtractedTransaction = self.extractor.extract(clean_text, products)

        # Tolak pesan yang mencampur penjualan dan pembelian tanpa memanggil LLM lagi
        if extracted.type == ExtractedTransactionType.MIXED:
            return MIXED_TRANSACTION_REPLY, None

        if not extracted.items:
            return (
                "Maaf, rincian barang belum terbaca. Mohon tulis nama barang dan jumlahnya.",
                None,
            )

        tx_type = (
            TransactionType.SALE
            if extracted.type == ExtractedTransactionType.SALE
            else TransactionType.PURCHASE
        )

        # Validasi barang tidak dikenali
        for item in extracted.items:
            if item.product_id is None or item.product_id not in products_by_id:
                reply = (
                    f"Maaf, barang mana yang dimaksud untuk '{item.product_name_raw}'? "
                    "Produk tidak ditemukan di daftar produk toko."
                )
                return reply, None

        # Validasi harga beli jika pembelian stok
        if tx_type == TransactionType.PURCHASE:
            for item in extracted.items:
                if item.unit_price is None or item.unit_price <= Decimal("0.00"):
                    return (
                        f"Berapa harga beli atau kulakan untuk '{item.product_name_raw}'?",
                        None,
                    )

        # Persiapkan draft transaksi pending
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
            if tx_type == TransactionType.SALE and item.qty > product.stock_qty:
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

        # Buat transaksi berstatus pending
        tx = tx_repo.create_pending_transaction(
            tx_type=tx_type.value,
            source=TransactionSource.CHAT.value,
            raw_text=clean_text,
            items=items_payload,
        )

        # Susun teks konfirmasi template
        reply = _build_confirmation_text(tx_type, matched_items, warnings)
        return reply, tx.id
