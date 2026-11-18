import csv
import io

FIELDS = ["business_key", "line_no", "document_id", "reason_code", "status",
          "amount_cny", "invoice_amount_cny", "difference_cny", "receipt_ids"]


def differences_csv(rows: list[dict]) -> str:
    """业务导出只保留一份证据清单，字段固定、金额为字符串。"""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        receipt_ids = row.get("receipt_ids", [])
        if isinstance(receipt_ids, list):
            receipt_ids = " ".join(
                " ".join(item) if isinstance(item, list) else str(item)
                for item in receipt_ids
            )
        writer.writerow({
            "business_key": row.get("business_key", ""),
            "line_no": row.get("line_no", ""),
            "document_id": row.get("invoice_id") or row.get("document_id") or "",
            "reason_code": row.get("reason_code", ""),
            "status": row.get("status", ""),
            "amount_cny": row.get("amount", row.get("amount_cny", "")),
            "invoice_amount_cny": row.get("invoice_amount", ""),
            "difference_cny": row.get("difference", ""),
            "receipt_ids": receipt_ids,
        })
    return output.getvalue()


def evidence_rows(result: dict, quarantined: list[dict] | None = None) -> list[dict]:
    rows = [dict(line) for line in result.get("lines", [])]
    for item in quarantined or []:
        rows.append({
            "business_key": "",
            "line_no": item.get("line_no", ""),
            "invoice_id": item.get("document_id", ""),
            "reason_code": item.get("reason_code", ""),
            "status": "QUARANTINED",
            "amount": "",
            "invoice_amount": "",
            "difference": "",
            "receipt_ids": [],
        })
    return rows
