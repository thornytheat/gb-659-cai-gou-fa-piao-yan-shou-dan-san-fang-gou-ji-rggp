from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse
from app.web import page
from .service import demo, preview, run

router = APIRouter()


@router.get("/matching", response_class=HTMLResponse)
def view(request: Request):
    result = preview()
    rows = [
        {"key": pairing["business_key"], "status": approval_label(pairing["approval"]),
         "value": pairing["receipt_amount"], "detail": pairing}
        for pairing in result["pairings"]
    ] + [
        {"key": item["business_key"], "status": reason_label(item["reason_code"]),
         "value": item["amount"], "detail": item}
        for item in result["pending"]
    ]
    return page(request, "m5.html", "多对多匹配", "/api/matches/solve", rows,
                extra={"result": result, "tabs": build_tabs(result)})


def approval_label(value: str) -> str:
    return {"KEPT": "保留核准", "MOVED": "挪配", "NEW": "新配对"}.get(value, value)


def reason_label(value: str) -> str:
    return {
        "RECEIPT_HELD_BY_APPROVED": "被核准配对占用",
        "RECEIPT_OCCUPIED": "验收单已占用",
        "NO_EQUAL_SUBSET": "无等额组合",
        "NO_RECEIPT": "无可用验收单",
        "APPROVED_RECEIPT_MISSING": "核准验收单缺失",
    }.get(value, value)


def build_tabs(result: dict) -> list[dict]:
    return [
        {"name": "pairings", "label": "已配对", "rows": [
            {"a": item["invoice_id"], "b": " + ".join(item["receipt_ids"]),
             "c": item["receipt_amount"], "d": approval_label(item["approval"])}
            for item in result["pairings"]
        ]},
        {"name": "pending", "label": "待处理", "rows": [
            {"a": item["invoice_id"], "b": reason_label(item["reason_code"]),
             "c": item["amount"],
             "d": " / ".join(" + ".join(ids) for ids in item["candidate_receipt_ids"]) or "—"}
            for item in result["pending"]
        ]},
        {"name": "uninvoiced", "label": "未开票", "rows": [
            {"a": item["receipt_id"], "b": item["business_key"],
             "c": item["amount"], "d": "UNINVOICED"}
            for item in result["uninvoiced"]
        ]},
    ]


@router.post("/api/matches/solve")
def execute(payload: Any = Body(default=None)):
    # 无请求体：保留单票样例；空对象（页面按钮）：展示完整分配样例。
    if payload is None:
        return run()
    return run(payload) if payload else demo()


@router.get("/api/matches/sample")
def sample():
    return demo()
