import json
from decimal import Decimal

from app.manifest import build_manifest
from app.pipeline import demo_request, normalize_request, solve_request
from app.store import batch_summary, load_batch, save_batch
from .compare import reconcile_result


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _build(payload: dict | None) -> tuple[dict, dict, dict, object]:
    payload = demo_request() if payload is None else payload
    normalized = normalize_request(payload)
    result = solve_request(normalized)
    compare = reconcile_result(result, tolerance=Decimal(str(normalized["tolerance"])))
    manifest = build_manifest(canonical(payload), normalized["accepted"],
                              len(normalized["quarantined"]))
    return manifest, normalized, result, compare


def preview(payload: dict | None = None) -> dict:
    """页面展示用：计算但不落库。"""
    manifest, normalized, result, compare = _build(payload)
    return {
        **manifest,
        "created": False,
        "reused": False,
        "totals": result["totals"],
        "reconcile": compare,
        "pairings": result["pairings"],
        "pending": result["pending"],
        "uninvoiced": result["uninvoiced"],
        "quarantined": result["quarantined"],
        "storage": {"pairings": [], "pending": [],
                    "receipt_usage": {"rows": 0, "distinct": 0}},
        "stored": False,
    }


def run(payload: dict | None = None) -> dict:
    """求解、按实际配对金额对账并落批次；同输入重跑复用已有批次。"""
    payload = demo_request() if payload is None else payload
    normalized = normalize_request(payload)
    request_bytes = canonical(payload)
    result = solve_request(normalized)
    tolerance = Decimal(str(normalized["tolerance"]))
    compare = reconcile_result(result, tolerance=tolerance)

    manifest = build_manifest(request_bytes, normalized["accepted"],
                              len(normalized["quarantined"]))
    manifest["created"] = save_batch(manifest, {
        "pairings": result["pairings"],
        "pending": result["pending"],
        "uninvoiced": result["uninvoiced"],
        "totals": result["totals"],
        "reconcile": compare,
    })
    stored = None if manifest["created"] else load_batch(manifest["run_id"])
    # 复用批次时以已存结果为唯一口径，避免同批次前后两次响应不一致。
    answer = {
        **manifest,
        "created": manifest["created"],
        "reused": not manifest["created"],
        "totals": result["totals"],
        "reconcile": compare,
        "pairings": result["pairings"],
        "pending": result["pending"],
        "uninvoiced": result["uninvoiced"],
        "quarantined": result["quarantined"],
        "storage": batch_summary(manifest["run_id"]),
        "stored": stored is not None,
    }
    if stored:
        for key in ("totals", "reconcile", "pairings", "pending", "uninvoiced"):
            if key in stored:
                answer[key] = stored[key]
    return answer


def demo() -> dict:
    return run()
