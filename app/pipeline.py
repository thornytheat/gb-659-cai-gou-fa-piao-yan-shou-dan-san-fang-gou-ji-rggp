"""把单据采信（M1）与主数据映射（M2）的结果喂给匹配（M5/M6）。

匹配只允许使用 M1 判定为 ``accepted`` 的单据；被隔离的行保留行号与
原因码作为证据，但绝不进入组合候选。
"""

from decimal import Decimal, InvalidOperation

from app.modules.m1_documents.money import decimal_value
from app.modules.m1_documents.parser import parse_documents
from app.modules.m2_master.aliases import normalize_sku, resolve_alias

DEFAULT_ALIASES = {"OLD-A": "SKU-A", "A-01": "SKU-A", "000143": "SKU-B"}
DOC_TYPES = {"PO", "GR", "RT", "IV"}


def _rate(text) -> str:
    token = str(text).strip().replace("％", "%").replace("%", "")
    try:
        value = Decimal(token)
    except InvalidOperation as exc:
        raise ValueError("BAD_FORMAT") from exc
    if value > 1:
        value = value / 100
    if value < 0:
        raise ValueError("BAD_FORMAT")
    return str(value.normalize())


def _row_reason(row: dict, required: tuple[str, ...]) -> str | None:
    if any(not str(row.get(key, "")).strip() for key in required):
        return "EMPTY_VALUE"
    return None


def _resolve_sku(value: str, aliases: dict[str, str]) -> str:
    normalized = normalize_sku(value)
    return resolve_alias(normalized, aliases)


def _ingest_documents(rows: list[dict], aliases: dict[str, str], start_line: int = 2) -> dict:
    accepted_rows, quarantined = parse_documents(rows, start_line=start_line)
    receipts, invoices, returns, purchase_orders = [], [], [], []

    for row in accepted_rows:
        doc_type = str(row["doc_type"]).strip().upper()
        if doc_type not in DOC_TYPES:
            quarantined.append({"line_no": row.get("line_no"), "reason_code": "UNKNOWN_DOC_TYPE",
                                "document_id": row.get("doc_no"), "raw": row})
            continue
        try:
            sku = _resolve_sku(row["sku"], aliases)
        except ValueError as exc:
            quarantined.append({"line_no": row.get("line_no"), "reason_code": "ALIAS_CYCLE",
                                "document_id": row.get("doc_no"), "raw": row})
            continue
        except (TypeError, AttributeError):
            quarantined.append({"line_no": row.get("line_no"), "reason_code": "BAD_MASTER_DATA",
                                "document_id": row.get("doc_no"), "raw": row})
            continue
        try:
            amount = decimal_value(row["amount"])
            tax_rate = _rate(row["tax_rate"])
        except (ValueError, InvalidOperation) as exc:
            code = "EMPTY_VALUE" if str(exc) == "EMPTY_AMOUNT" else "BAD_FORMAT"
            quarantined.append({"line_no": row.get("line_no"), "reason_code": code,
                                "document_id": row.get("doc_no"), "raw": row})
            continue
        document = {
            "id": str(row["doc_no"]),
            "doc_no": str(row["doc_no"]),
            "doc_type": doc_type,
            "sku": sku,
            "tax_rate": tax_rate,
            "amount": str(amount),
            "qty": str(row.get("qty", "")),
            "unit": row.get("unit"),
            "supplier_code": row.get("supplier_code"),
            "line_no": row.get("line_no"),
        }
        if doc_type == "GR":
            if amount <= 0:
                returns.append({**document, "reason_code": "NON_POSITIVE_RECEIPT"})
            else:
                receipts.append(document)
        elif doc_type == "IV":
            if amount <= 0:
                quarantined.append({"line_no": row.get("line_no"), "reason_code": "NON_POSITIVE_INVOICE",
                                    "document_id": document["id"], "raw": row})
            else:
                invoices.append(document)
        elif doc_type == "RT":
            returns.append({**document, "reason_code": "RETURN"})
        else:
            purchase_orders.append(document)

    return {
        "receipts": receipts,
        "invoices": invoices,
        "returns": returns,
        "purchase_orders": purchase_orders,
        "quarantined": quarantined,
        "accepted_count": len(receipts) + len(invoices) + len(returns) + len(purchase_orders),
    }


def _explicit(rows: list[dict], doc_type: str, aliases: dict[str, str]) -> tuple[list[dict], list[dict]]:
    accepted, rejected = [], []
    required = ("id", "sku", "amount", "tax_rate")
    seen: set[str] = set()
    for index, row in enumerate(rows, 2):
        reason = _row_reason(row, required)
        if reason:
            rejected.append({"line_no": row.get("line_no", index), "reason_code": reason, "raw": row})
            continue
        document_id = str(row["id"])
        if document_id in seen:
            rejected.append({"line_no": row.get("line_no", index), "reason_code": "DUPLICATE_KEY",
                             "document_id": document_id, "raw": row})
            continue
        try:
            sku = _resolve_sku(row["sku"], aliases)
            amount = decimal_value(row["amount"])
            tax_rate = _rate(row["tax_rate"])
        except (ValueError, InvalidOperation) as exc:
            code = str(exc) if str(exc) in ("ALIAS_CYCLE",) else "BAD_FORMAT"
            rejected.append({"line_no": row.get("line_no", index), "reason_code": code, "raw": row})
            continue
        if amount <= 0:
            rejected.append({"line_no": row.get("line_no", index), "reason_code": "NON_POSITIVE_DOCUMENT",
                             "raw": row})
            continue
        seen.add(document_id)
        accepted.append({
            "id": document_id,
            "doc_no": str(row.get("doc_no", document_id)),
            "doc_type": doc_type,
            "sku": sku,
            "tax_rate": tax_rate,
            "amount": str(amount),
            "qty": row.get("qty"),
            "unit": row.get("unit"),
            "supplier_code": row.get("supplier_code"),
            "line_no": row.get("line_no", index),
        })
    return accepted, rejected


def _unique_ids(rows: list[dict]) -> list[dict]:
    """同一单号出现在多行（如不同税率行）时，按出现顺序补后缀保证验收单/发票编号唯一。"""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["id"]] = counts.get(row["id"], 0) + 1
    seen: dict[str, int] = {}
    result = []
    for row in rows:
        if counts[row["id"]] > 1:
            seen[row["id"]] = seen.get(row["id"], 0) + 1
            result.append({**row, "id": f"{row['id']}#{seen[row['id']]}"})
        else:
            result.append(row)
    return result


def normalize_request(payload: dict | None, start_line: int = 2) -> dict:
    """把接口载荷整理成内部请求；宽松接受多种别名。

    ``documents`` 走 M1 采信与 M2 映射，只有被 accept 的行进入匹配；
    ``receipts``/``invoices`` 是已采信记录的显式入口（仍做主数据与金额校验）。
    """
    payload = payload or {}
    aliases = dict(DEFAULT_ALIASES)
    aliases.update({str(key): str(value) for key, value in (payload.get("aliases") or {}).items()})
    tolerance = str(payload.get("tolerance", "0.01"))
    limit = int(payload.get("limit", 4))
    policy = str(payload.get("policy", "settle"))
    approved = payload.get("approved") or payload.get("approved_pairings") or []
    documents = payload.get("documents")
    explicit_receipts = payload.get("receipts") or []
    explicit_invoices = payload.get("invoices") or []

    ingested = _ingest_documents(documents, aliases, start_line) if documents is not None else {
        "receipts": [], "invoices": [], "returns": [], "purchase_orders": [],
        "quarantined": [], "accepted_count": 0,
    }
    accepted_count = ingested["accepted_count"]
    quarantined = ingested["quarantined"]
    receipts = list(ingested["receipts"])
    invoices = list(ingested["invoices"])
    returns = list(ingested["returns"])

    for rows, doc_type in ((explicit_receipts, "GR"), (explicit_invoices, "IV")):
        extra, rejected = _explicit(rows, doc_type, aliases)
        quarantined.extend(rejected)
        accepted_count += len(extra)
        if doc_type == "GR":
            receipts.extend(extra)
        else:
            invoices.extend(extra)

    receipts = _unique_ids(receipts)
    invoices = _unique_ids(invoices)
    returns = _unique_ids(returns)

    return {
        "documents": documents or [],
        "receipts": receipts,
        "invoices": invoices,
        "returns": returns,
        "purchase_orders": ingested["purchase_orders"],
        "approved": [
            {"invoice_id": str(item.get("invoice_id") or item.get("invoice")),
             "receipt_ids": [str(receipt_id) for receipt_id in item.get("receipt_ids", [])]}
            for item in approved
        ],
        "tolerance": tolerance,
        "limit": limit,
        "policy": policy,
        "aliases": aliases,
        "accepted": accepted_count,
        "quarantined": quarantined,
    }


def solve_request(normalized: dict) -> dict:
    from app.modules.m5_matching.allocator import allocate

    result = allocate(
        normalized["receipts"], normalized["invoices"],
        approved=normalized["approved"],
        tolerance=normalized["tolerance"],
        limit=normalized["limit"],
        policy=normalized["policy"],
    )
    result["returns"] = normalized["returns"]
    result["quarantined"] = normalized["quarantined"]
    result["purchase_order_count"] = len(normalized["purchase_orders"])
    result["accepted"] = normalized["accepted"]
    return result


def demo_request() -> dict:
    """演示三种典型局面：

    * SKU-A：新票只能配已核准发票占用的 GR-A2；旧票另有一组等额验收单，
      默认策略挪配让两张都结算，``policy="stable"`` 则保留原配对、新票挂起。
    * SKU-B：新票的唯一候选被已核准配对占用，且旧票没有等额替代组。
    * SKU-C：没有等额组合，只能挂起。
    """
    return {
        "receipts": [
            {"id": "GR-A1", "sku": "SKU-A", "tax_rate": "0.13", "amount": "600.00", "line_no": 2},
            {"id": "GR-A2", "sku": "SKU-A", "tax_rate": "0.13", "amount": "400.00", "line_no": 3},
            {"id": "GR-A3", "sku": "SKU-A", "tax_rate": "0.13", "amount": "550.00", "line_no": 4},
            {"id": "GR-A4", "sku": "SKU-A", "tax_rate": "0.13", "amount": "450.00", "line_no": 5},
            {"id": "GR-B1", "sku": "SKU-B", "tax_rate": "0.09", "amount": "500.00", "line_no": 6},
            {"id": "GR-C1", "sku": "SKU-C", "tax_rate": "0.13", "amount": "300.00", "line_no": 7},
        ],
        "invoices": [
            {"id": "IV-A1", "sku": "SKU-A", "tax_rate": "0.13", "amount": "1000.00", "line_no": 8},
            {"id": "IV-A2", "sku": "SKU-A", "tax_rate": "0.13", "amount": "400.00", "line_no": 9},
            {"id": "IV-B1", "sku": "SKU-B", "tax_rate": "0.09", "amount": "500.00", "line_no": 10},
            {"id": "IV-B2", "sku": "SKU-B", "tax_rate": "0.09", "amount": "500.00", "line_no": 11},
            {"id": "IV-C1", "sku": "SKU-C", "tax_rate": "0.13", "amount": "900.00", "line_no": 12},
        ],
        "approved": [
            {"invoice_id": "IV-A1", "receipt_ids": ["GR-A1", "GR-A2"]},
            {"invoice_id": "IV-B1", "receipt_ids": ["GR-B1"]},
        ],
    }


def demo_solve() -> dict:
    normalized = normalize_request(demo_request())
    result = solve_request(normalized)
    result["request"] = demo_request()
    result["demo_request"] = demo_request()
    return result
