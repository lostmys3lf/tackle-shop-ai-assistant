"""Evaluation runner for LLM transaction extraction and intent routing."""

import csv
import json
import os
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.domain.models import (
    DomainProduct,
    TransactionType,
)  # pylint: disable=wrong-import-position
from app.infrastructure.llm.extractor import (
    TransactionExtractor,
)  # pylint: disable=wrong-import-position
from app.infrastructure.llm.router import (
    LLMMessageRouter,
)  # pylint: disable=wrong-import-position

EVAL_THRESHOLD = 0.85
DELAY_BETWEEN_CALLS = 1.5  # Jeda detik agar mematuhi batas 30 req/menit Groq


@dataclass
class FieldScore:
    """Store matched count and total count for a specific field."""

    correct: int = 0
    total: int = 0

    @property
    def accuracy(self) -> float:
        """Calculate accuracy ratio."""
        return self.correct / self.total if self.total > 0 else 1.0


def load_seed_products() -> list[DomainProduct]:
    """Load product catalog from db/seed/products.csv."""
    csv_path = BASE_DIR / "db" / "seed" / "products.csv"
    products: list[DomainProduct] = []
    with open(csv_path, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for idx, row in enumerate(reader, start=1):
            products.append(
                DomainProduct(
                    id=idx,
                    name=row["name"],
                    category=row["category"],
                    unit=row["unit"],
                    sell_price=Decimal(row["sell_price"]),
                    avg_cost=Decimal(row["purchase_price"]),
                    stock_qty=int(row["stock_qty"]),
                    min_stock=int(row["min_stock"]),
                )
            )
    return products


def evaluate_router(
    router: LLMMessageRouter, limit: int | None = None
) -> tuple[FieldScore, list[dict[str, Any]]]:
    """Evaluate router on router.jsonl dataset."""
    dataset_path = BASE_DIR / "evals" / "datasets" / "router.jsonl"
    score = FieldScore()
    details: list[dict[str, Any]] = []

    with open(dataset_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    if limit:
        lines = lines[:limit]

    print(f"\n--- Mengevaluasi Router Intent ({len(lines)} contoh) ---")
    for line in lines:
        sample = json.loads(line)
        text = sample["text"]
        expected = sample["expected_intent"]

        try:
            res = router.route(text)
            actual = res.intent.value
            is_match = actual == expected
        except Exception as exc:  # pylint: disable=broad-exception-caught
            actual = f"ERROR: {exc}"
            is_match = False

        score.total += 1
        if is_match:
            score.correct += 1

        details.append(
            {
                "id": sample["id"],
                "text": text,
                "expected": expected,
                "actual": actual,
                "match": is_match,
            }
        )
        print(
            f"[{'OK' if is_match else 'FAIL'}] '{text}' -> Expected: {expected}, Got: {actual}"
        )
        time.sleep(DELAY_BETWEEN_CALLS)

    return score, details


def _evaluate_single_transaction(
    extractor: TransactionExtractor,
    sample: dict[str, Any],
    products: list[DomainProduct],
    scores: dict[str, FieldScore],
) -> dict[str, Any]:
    """Extract and score a single transaction example."""
    text = sample["text"]
    expected = sample["expected"]
    expected_type = expected["type"]
    expected_items = expected["items"]

    try:
        extracted = extractor.extract(text, products)
        actual_type = extracted.type.value
        actual_items = extracted.items
    except Exception as exc:  # pylint: disable=broad-exception-caught
        actual_type = "error"
        actual_items = []
        print(f"[ERROR] Ekstraksi gagal pada '{text}': {exc}")

    # 1. Skor tipe transaksi
    scores["type"].total += 1
    type_match = actual_type == expected_type
    if type_match:
        scores["type"].correct += 1

    # 2. Skor kecocokan jumlah item
    scores["item_count"].total += 1
    count_match = len(actual_items) == len(expected_items)
    if count_match:
        scores["item_count"].correct += 1

    # 3. Skor per item (product_id, qty, unit_price)
    for exp_item, act_item in zip(expected_items, actual_items):
        # Product ID
        scores["product_id"].total += 1
        if act_item.product_id == exp_item["product_id"]:
            scores["product_id"].correct += 1

        # Qty
        scores["qty"].total += 1
        if act_item.qty == exp_item["qty"]:
            scores["qty"].correct += 1

        # Unit price
        scores["unit_price"].total += 1
        exp_price = exp_item["unit_price"]
        act_price = (
            float(act_item.unit_price) if act_item.unit_price is not None else None
        )
        if (exp_price is None and act_price is None) or (
            exp_price is not None
            and act_price is not None
            and abs(exp_price - act_price) < 0.01
        ):
            scores["unit_price"].correct += 1

    # Jika actual item kurang dari expected item, hitung penalty
    if len(actual_items) < len(expected_items):
        missing = len(expected_items) - len(actual_items)
        scores["product_id"].total += missing
        scores["qty"].total += missing
        scores["unit_price"].total += missing

    sample_passed = type_match and count_match
    return {
        "id": sample["id"],
        "text": text,
        "passed": sample_passed,
        "expected": expected,
        "actual": {
            "type": actual_type,
            "items": [item.model_dump() for item in actual_items],
        },
    }


def evaluate_transactions(
    extractor: TransactionExtractor,
    products: list[DomainProduct],
    limit: int | None = None,
) -> tuple[dict[str, FieldScore], list[dict[str, Any]]]:
    """Evaluate extractor on transactions.jsonl dataset."""
    dataset_path = BASE_DIR / "evals" / "datasets" / "transactions.jsonl"
    scores = {
        "type": FieldScore(),
        "item_count": FieldScore(),
        "product_id": FieldScore(),
        "qty": FieldScore(),
        "unit_price": FieldScore(),
    }
    details: list[dict[str, Any]] = []

    with open(dataset_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    if limit:
        lines = lines[:limit]

    print(f"\n--- Mengevaluasi Ekstraksi Transaksi ({len(lines)} contoh) ---")
    for line in lines:
        sample = json.loads(line)
        res_detail = _evaluate_single_transaction(extractor, sample, products, scores)
        details.append(res_detail)
        status = "OK" if res_detail["passed"] else "FAIL"
        print(f"[{status}] {sample['id']}: '{sample['text']}'")
        time.sleep(DELAY_BETWEEN_CALLS)

    return scores, details


def record_to_langfuse(report: dict[str, Any]) -> None:
    """Record evaluation run to Langfuse if credentials are configured."""
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        print(
            "[INFO] Langfuse tidak dikonfigurasi. Melewatkan pencatatan dataset run ke Langfuse."
        )
        return

    try:
        from langfuse import Langfuse  # pylint: disable=import-outside-toplevel

        lf = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
        dataset_name = "transactions_eval_suite"

        print(f"\nMencatat hasil evaluasi ke Langfuse dataset: '{dataset_name}'...")
        try:
            dataset = lf.get_dataset(dataset_name)
        except Exception:  # pylint: disable=broad-exception-caught
            dataset = lf.create_dataset(
                name=dataset_name, description="Dataset evaluasi transaksi dan routing"
            )

        # Catat dataset run
        run_name = f"eval-run-{int(time.time())}"
        for item in report.get("transactions_details", []):
            try:
                # Simpan atau update item di dataset
                dataset_item = lf.create_dataset_item(
                    dataset_name=dataset_name,
                    input=item["text"],
                    expected_output=item["expected"],
                )
                dataset_item.link(
                    trace_or_observation=None,
                    run_name=run_name,
                    metadata={"passed": item["passed"], "actual": item["actual"]},
                )
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        lf.flush()
        print(f"[OK] Berhasil mencatat dataset run '{run_name}' ke Langfuse.")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[WARN] Gagal mencatat ke Langfuse: {exc}")


def print_score_table(scores: dict[str, FieldScore], router_score: FieldScore) -> float:
    """Print markdown/table format of evaluation scores and return average accuracy."""
    print("\n" + "=" * 60)
    print(f"{'Field / Kategori':<25} | {'Benar':<8} | {'Total':<8} | {'Akurasi':<8}")
    print("-" * 60)

    print(
        f"{'Intent Routing':<25} | {router_score.correct:<8} | {router_score.total:<8} | {router_score.accuracy * 100:.1f}%"
    )

    total_correct = router_score.correct
    total_items = router_score.total

    for name, score in scores.items():
        print(
            f"{name:<25} | {score.correct:<8} | {score.total:<8} | {score.accuracy * 100:.1f}%"
        )
        total_correct += score.correct
        total_items += score.total

    overall_acc = total_correct / total_items if total_items > 0 else 0.0
    print("-" * 60)
    print(
        f"{'TOTAL RATA-RATA':<25} | {total_correct:<8} | {total_items:<8} | {overall_acc * 100:.1f}%"
    )
    print("=" * 60)
    return overall_acc


def main() -> None:
    """Main execution function for evaluation suite."""
    limit = None
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        limit = int(sys.argv[1])
        print(f"[INFO] Menjalankan eval dengan limit {limit} contoh.")

    products = load_seed_products()
    router = LLMMessageRouter()
    extractor = TransactionExtractor()

    start_time = time.time()

    router_score, router_details = evaluate_router(router, limit=limit)
    tx_scores, tx_details = evaluate_transactions(extractor, products, limit=limit)

    overall_accuracy = print_score_table(tx_scores, router_score)
    duration = time.time() - start_time
    print(f"Evaluasi selesai dalam {duration:.1f} detik.")

    report = {
        "timestamp": time.time(),
        "overall_accuracy": overall_accuracy,
        "threshold": EVAL_THRESHOLD,
        "router_accuracy": router_score.accuracy,
        "transaction_field_accuracies": {k: v.accuracy for k, v in tx_scores.items()},
        "router_details": router_details,
        "transactions_details": tx_details,
    }

    report_path = BASE_DIR / "evals" / "eval_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"Laporan evaluasi tersimpan di: {report_path}")

    record_to_langfuse(report)

    if overall_accuracy < EVAL_THRESHOLD:
        print(
            f"\n[GAGAL] Akurasi total {overall_accuracy:.2f} di bawah threshold {EVAL_THRESHOLD:.2f}."
        )
        sys.exit(1)

    print(
        f"\n[LULUS] Akurasi total {overall_accuracy:.2f} memenuhi threshold {EVAL_THRESHOLD:.2f}."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
