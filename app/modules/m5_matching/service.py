from decimal import Decimal

from app.pipeline import demo_request as allocation_demo
from app.pipeline import normalize_request, solve_request
from .candidates import grouped_candidates
from .solver import subset_match

SAMPLE_RECEIPTS = [
    {"id": "GR-1", "sku": "SKU-A", "tax_rate": "0.13", "amount": "600.00"},
    {"id": "GR-2", "sku": "SKU-A", "tax_rate": "0.13", "amount": "530.00"},
]
SAMPLE_INVOICE = {"id": "IV-1", "sku": "SKU-A", "tax_rate": "0.13", "amount": "1130.00"}


def run(payload: dict | None = None) -> dict:
    """有载荷时走完整分配；无载荷时保留单张发票的经典样例。"""
    if payload:
        return solve_request(normalize_request(payload))
    candidates = grouped_candidates(SAMPLE_RECEIPTS, SAMPLE_INVOICE)
    selected = subset_match(candidates, Decimal(SAMPLE_INVOICE["amount"]))
    return {
        "invoice": SAMPLE_INVOICE["id"],
        "matched": [row["id"] for row in selected],
        "amount": str(sum((Decimal(row["amount"]) for row in selected), start=Decimal("0"))),
    }


def _demo_result() -> dict:
    result = solve_request(normalize_request(allocation_demo()))
    result["amount"] = result["totals"]["matched_invoice_amount"]
    return result


def preview() -> dict:
    """页面展示用：计算但不落库。"""
    return _demo_result()


def demo() -> dict:
    """样例：已核准配对稳定，但挪配可让新旧两张发票都结算。"""
    return _demo_result()
