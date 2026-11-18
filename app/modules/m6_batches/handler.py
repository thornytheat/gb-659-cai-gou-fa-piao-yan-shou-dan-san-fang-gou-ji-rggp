from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from app.web import page
from .exporter import differences_csv, evidence_rows
from .service import demo, preview, run

router = APIRouter()

REASON_LABELS = {
    "UNINVOICED": "未开票",
    "RECEIPT_HELD_BY_APPROVED": "被核准配对占用",
    "RECEIPT_OCCUPIED": "验收单已占用",
    "NO_EQUAL_SUBSET": "无等额组合",
    "NO_RECEIPT": "无可用验收单",
    "APPROVED_RECEIPT_MISSING": "核准验收单缺失",
    "MATCHED": "已配对",
}

APPROVAL_LABELS = {"KEPT": "保留核准", "MOVED": "挪配", "NEW": "新配对"}


def _tabs(result: dict) -> list[dict]:
    compare = result["reconcile"]
    return [
        {"name": "pairings", "label": "实际配对", "rows": [
            {"a": pairing["invoice_id"], "b": " + ".join(pairing["receipt_ids"]),
             "c": pairing["receipt_amount"], "d": APPROVAL_LABELS.get(pairing["approval"], pairing["approval"]),
             "e": pairing["difference"]}
            for pairing in result["pairings"]
        ]},
        {"name": "pending", "label": "待处理", "rows": [
            {"a": item["invoice_id"], "b": REASON_LABELS.get(item["reason_code"], item["reason_code"]),
             "c": item["amount"],
             "d": " / ".join(" + ".join(ids) for ids in item["candidate_receipt_ids"]) or "—",
             "e": ""}
            for item in result["pending"]
        ]},
        {"name": "uninvoiced", "label": "未开票验收", "rows": [
            {"a": item["receipt_id"], "b": item.get("business_key", ""),
             "c": item["amount"], "d": "UNINVOICED", "e": item["amount"]}
            for item in result["uninvoiced"]
        ]},
        {"name": "evidence", "label": "隔离证据", "rows": [
            {"a": item.get("document_id") or "—", "b": item.get("reason_code", ""),
             "c": str(item.get("line_no") or ""), "d": "隔离", "e": ""}
            for item in result.get("quarantined", [])
        ]},
    ], compare


@router.get("/batches", response_class=HTMLResponse)
def view(request: Request):
    result = preview()
    compare = result["reconcile"]
    rows = [
        {"key": f"配对 {pairing['invoice_id']}",
         "status": APPROVAL_LABELS.get(pairing["approval"], pairing["approval"]),
         "value": pairing["receipt_amount"]}
        for pairing in result["pairings"]
    ] + [
        {"key": f"待处理 {item['invoice_id']}",
         "status": REASON_LABELS.get(item["reason_code"], item["reason_code"]),
         "value": item["amount"]}
        for item in result["pending"]
    ] + [
        {"key": f"未开票 {item['receipt_id']}", "status": "UNINVOICED",
         "value": item["amount"]}
        for item in result["uninvoiced"]
    ]
    tabs, compare = _tabs(result)
    return page(request, "m6.html", "差异批次", "/api/reconcile", rows,
                extra={"result": result, "tabs": tabs, "compare": compare})


@router.post("/api/reconcile")
def execute(payload: Any = Body(default=None)):
    if payload is None or payload == {}:
        return run()
    return run(payload)


@router.get("/api/export/differences.csv")
def export_differences(run_id: str | None = None):
    from app.store import load_batch
    stored = load_batch(run_id) if run_id else None
    if stored:
        lines = stored.get("reconcile", {}).get("lines", [])
        body = differences_csv(lines)
    else:
        result = preview()
        body = differences_csv(evidence_rows(result["reconcile"], result["quarantined"]))
    return PlainTextResponse(body, media_type="text/csv",
                             headers={"content-disposition": "attachment; filename=differences.csv"})
