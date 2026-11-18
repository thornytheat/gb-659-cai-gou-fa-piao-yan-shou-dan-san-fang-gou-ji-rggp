"""差异批次核对。

对账只使用实际配对金额：每张发票按配对里验收单的实际金额合计，
验收单在同一批次内只能出现在一个配对中。发票已核准但尚未配对、
验收单未开票也都单独列出。
"""

from decimal import Decimal


def reconcile_amounts(received: str, invoiced: str, uninvoiced: str, difference: str) -> dict:
    values = [Decimal(value) for value in (received, invoiced, uninvoiced, difference)]
    balanced = values[0] == values[1] + values[2] + values[3]
    return {"received": str(values[0]), "invoiced": str(values[1]),
            "uninvoiced": str(values[2]), "difference": str(values[3]),
            "balanced": balanced}


def reconcile_counts(input_count: int, accepted: int, quarantined: int, duplicates: int) -> dict:
    return {"input": input_count, "accepted": accepted, "quarantined": quarantined,
            "duplicates": duplicates,
            "balanced": input_count == accepted + quarantined + duplicates}


def receipt_usage(pairings: list[dict]) -> dict:
    """统计验收单占用；重复出现说明同一张验收单被算给了多张发票。"""
    counts: dict[str, int] = {}
    for pairing in pairings:
        for receipt_id in pairing["receipt_ids"]:
            counts[receipt_id] = counts.get(receipt_id, 0) + 1
    duplicated = sorted(receipt_id for receipt_id, count in counts.items() if count > 1)
    return {
        "rows": sum(counts.values()),
        "distinct": len(counts),
        "duplicated": duplicated,
        "unique": not duplicated,
    }


def reconcile_result(result: dict, tolerance: Decimal = Decimal("0.01")) -> dict:
    pairings = result.get("pairings", [])
    pending = result.get("pending", [])
    uninvoiced = result.get("uninvoiced", [])
    totals = result.get("totals", {})

    matched_receipt = Decimal(totals.get("matched_receipt_amount", "0"))
    matched_invoice = Decimal(totals.get("matched_invoice_amount", "0"))
    uninvoiced_amount = Decimal(totals.get("uninvoiced_amount", "0"))
    accepted_receipt = Decimal(totals.get("accepted_receipt_amount", "0"))
    accepted_invoice = Decimal(totals.get("accepted_invoice_amount", "0"))
    pending_invoice = Decimal(totals.get("pending_invoice_amount", "0"))

    # 每张发票按实际配对金额（验收单金额合计）对账，而不是按发票面值。
    explained = Decimal("0")
    within_tolerance = True
    for pairing in pairings:
        difference = Decimal(pairing["receipt_amount"]) - Decimal(pairing["amount"])
        explained += difference
        if abs(difference) > tolerance:
            within_tolerance = False

    receipts = reconcile_amounts(str(accepted_receipt), str(matched_receipt),
                                 str(uninvoiced_amount),
                                 str(accepted_receipt - matched_receipt - uninvoiced_amount))
    invoices = reconcile_amounts(str(accepted_invoice), str(matched_invoice),
                                 str(pending_invoice),
                                 str(accepted_invoice - matched_invoice - pending_invoice))
    pairings_check = reconcile_amounts(str(matched_receipt), str(matched_invoice),
                                       str(explained), "0")
    usage = receipt_usage(pairings)
    accepted_documents = int(result.get("accepted", len(pairings) + len(pending)))
    quarantined_documents = len(result.get("quarantined", []))

    lines = []
    for pairing in sorted(pairings, key=lambda item: item["invoice_id"]):
        actual = Decimal(pairing["receipt_amount"])
        invoiced_amount = Decimal(pairing["amount"])
        lines.append({
            "business_key": pairing.get("business_key", ""),
            "invoice_id": pairing["invoice_id"],
            "receipt_ids": pairing["receipt_ids"],
            "amount": str(actual),
            "invoice_amount": str(invoiced_amount),
            "difference": str(actual - invoiced_amount),
            "approval": pairing.get("approval", "NEW"),
            "reason_code": "MATCHED",
            "status": "BALANCED" if actual == invoiced_amount else "WITHIN_TOLERANCE",
        })
    for item in sorted(pending, key=lambda row: row["invoice_id"]):
        lines.append({
            "business_key": item.get("business_key", ""),
            "invoice_id": item["invoice_id"],
            "receipt_ids": item.get("candidate_receipt_ids", []),
            "amount": item["amount"],
            "invoice_amount": item["amount"],
            "difference": "0.00",
            "approval": "PENDING",
            "reason_code": item["reason_code"],
            "status": "PENDING",
        })
    for item in sorted(uninvoiced, key=lambda row: row["receipt_id"]):
        lines.append({
            "business_key": item.get("business_key", ""),
            "invoice_id": "",
            "receipt_ids": [item["receipt_id"]],
            "amount": item["amount"],
            "invoice_amount": "0.00",
            "difference": item["amount"],
            "approval": "UNINVOICED",
            "reason_code": "UNINVOICED",
            "status": "UNINVOICED",
        })

    return {
        "balanced": bool(receipts["balanced"] and invoices["balanced"]
                         and pairings_check["balanced"] and usage["unique"]
                         and within_tolerance),
        "receipts": receipts,
        "invoices": invoices,
        "pairings": pairings_check,
        "receipt_usage": usage,
        "counts": reconcile_counts(
            accepted_documents + quarantined_documents,
            accepted_documents, quarantined_documents, 0,
        ),
        "lines": lines,
        "matched_invoice_amount": str(matched_invoice),
        "matched_receipt_amount": str(matched_receipt),
        "matched_difference": str(explained),
        "uninvoiced_amount": str(uninvoiced_amount),
        "pending_invoice_amount": str(pending_invoice),
    }
