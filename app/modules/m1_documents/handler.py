import csv
import io
import json
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse
from app.web import page
from .parser import complete_tail, parse_documents
from .service import demo, run

router = APIRouter()


@router.get("/documents", response_class=HTMLResponse)
def view(request: Request):
    result = demo()
    rows = [
        {"key": item.get("doc_no", ""), "status": item.get("doc_type", ""),
         "value": item.get("amount", "")}
        for item in result.get("accepted_rows", [])
    ] + [
        {"key": item.get("document_id") or f"LINE {item.get('line_no')}",
         "status": item.get("reason_code", "隔离"), "value": "—"}
        for item in result.get("evidence", [])
    ]
    return page(request, "m1.html", "单据采信", "/api/documents/import", rows,
                extra={"result": result})


def _rows_from_payload(payload: Any) -> tuple[list[dict], int]:
    if isinstance(payload, list):
        return payload, 1
    if isinstance(payload, dict):
        rows = payload.get("documents") or payload.get("rows") or []
        start_line = 1 if payload.get("header") in (None, False) else 2
        return rows, start_line
    return [], 1


def _rows_from_csv(text: str) -> tuple[list[dict], int]:
    if not complete_tail(text):
        raise ValueError("TRUNCATED_INPUT")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader], 2


@router.post("/api/documents/import")
async def execute(request: Request):
    raw = await request.body()
    if not raw.strip():
        return demo()
    content_type = request.headers.get("content-type", "")
    if "csv" in content_type or request.headers.get("x-content-type") == "text/csv":
        text = raw.decode("utf-8-sig")
        try:
            rows, start_line = _rows_from_csv(text)
        except ValueError as exc:
            return {"input": 0, "accepted": 0, "quarantined": 1, "amount": "0",
                    "evidence": [{"line_no": None, "reason_code": str(exc)}]}
        return run(rows, start_line=start_line)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return demo()
    rows, start_line = _rows_from_payload(payload)
    if not rows:
        # 页面按钮发送空对象时展示样例，而不是空结果。
        return demo()
    return run(rows, start_line=start_line)


@router.post("/api/documents/validate")
def validate(payload: Any = Body(default=None)):
    if payload is None:
        return demo()
    rows, start_line = _rows_from_payload(payload)
    accepted, quarantined = parse_documents(rows, start_line=start_line)
    return {"input": len(rows), "accepted": len(accepted),
            "quarantined": len(quarantined), "evidence": quarantined}
