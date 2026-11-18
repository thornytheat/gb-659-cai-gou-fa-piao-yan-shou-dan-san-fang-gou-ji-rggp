from .parser import parse_documents
from .money import decimal_value

SAMPLE = [{"doc_type": "PO", "doc_no": "PO-001", "sku": "000142", "qty": "10", "amount": "1,000.00", "tax_rate": "0.13"}]


def run(rows: list[dict] | None = None, start_line: int = 2) -> dict:
    accepted, quarantined = parse_documents(rows if rows is not None else SAMPLE,
                                             start_line=start_line)
    amounts = []
    for row in accepted:
        try:
            amounts.append(decimal_value(row["amount"]))
        except ValueError:
            # 金额不可解析时保留行证据，但不计入金额合计。
            quarantined.append({"line_no": row.get("line_no"),
                                "reason_code": "BAD_FORMAT",
                                "document_id": row.get("doc_no"), "raw": row})
    total = sum(amounts, start=decimal_value("0"))
    return {
        "input": len(accepted) + len(quarantined),
        "accepted": len(accepted),
        "quarantined": len(quarantined),
        "amount": str(total),
        "evidence": quarantined,
        "accepted_rows": accepted,
    }


def demo():
    return run()
