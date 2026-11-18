import copy

import pytest

from app.modules.m5_matching.allocator import allocate
from app.modules.m6_batches.compare import reconcile_result, receipt_usage
from app.pipeline import demo_request, normalize_request, solve_request


def receipts(*rows):
    return [{"id": row[0], "sku": row[1], "tax_rate": row[2], "amount": row[3]}
            for row in rows]


def invoices(*rows):
    return [{"id": row[0], "sku": row[1], "tax_rate": row[2], "amount": row[3]}
            for row in rows]


def by_invoice(result):
    return {pairing["invoice_id"]: pairing for pairing in result["pairings"]}


# 已核准发票可挪到另一组等额验收单，让新旧两张发票都结算。
def test_move_settles_both_when_approved_has_equal_replacements():
    result = allocate(
        receipts(("GR-1", "A", "0.13", "600"), ("GR-2", "A", "0.13", "400"),
                 ("GR-3", "A", "0.13", "700"), ("GR-4", "A", "0.13", "300")),
        invoices(("IV-OLD", "A", "0.13", "1000"), ("IV-NEW", "A", "0.13", "400")),
        approved=[{"invoice_id": "IV-OLD", "receipt_ids": ["GR-1", "GR-2"]}],
    )
    pairs = by_invoice(result)
    assert set(pairs) == {"IV-OLD", "IV-NEW"}
    assert set(pairs["IV-OLD"]["receipt_ids"]) == {"GR-3", "GR-4"}
    assert pairs["IV-OLD"]["approval"] == "MOVED"
    assert pairs["IV-NEW"]["approval"] == "NEW"
    assert receipt_usage(result["pairings"])["unique"]


# 无需挪配也能全部结算时，保持原核准配对不动。
def test_approved_pairing_stays_when_alternative_exists_for_new_invoice():
    result = allocate(
        receipts(("GR-1", "A", "0.13", "600"), ("GR-2", "A", "0.13", "400"),
                 ("GR-3", "A", "0.13", "400")),
        invoices(("IV-OLD", "A", "0.13", "1000"), ("IV-NEW", "A", "0.13", "400")),
        approved=[{"invoice_id": "IV-OLD", "receipt_ids": ["GR-1", "GR-2"]}],
    )
    pairs = by_invoice(result)
    assert set(pairs) == {"IV-OLD", "IV-NEW"}
    assert set(pairs["IV-OLD"]["receipt_ids"]) == {"GR-1", "GR-2"}
    assert pairs["IV-OLD"]["approval"] == "KEPT"


# 旧票没有等额替代组时，保留原配对，新票挂起并说明原因。
def test_new_invoice_pending_when_approved_pairing_is_only_option():
    result = allocate(
        receipts(("GR-1", "A", "0.13", "600"), ("GR-2", "A", "0.13", "400")),
        invoices(("IV-OLD", "A", "0.13", "1000"), ("IV-NEW", "A", "0.13", "400")),
        approved=[{"invoice_id": "IV-OLD", "receipt_ids": ["GR-1", "GR-2"]}],
    )
    pairs = by_invoice(result)
    assert set(pairs["IV-OLD"]["receipt_ids"]) == {"GR-1", "GR-2"}
    assert pairs["IV-OLD"]["approval"] == "KEPT"
    pending = {item["invoice_id"]: item for item in result["pending"]}
    assert pending["IV-NEW"]["reason_code"] == "RECEIPT_HELD_BY_APPROVED"


# 显式 stable 策略：即使可以挪配也保留原配对，让新票挂起。
def test_stable_policy_keeps_approved_pairing():
    payload = demo_request()
    payload["policy"] = "stable"
    stable = solve_request(normalize_request(payload))
    pairs = by_invoice(stable)
    assert pairs["IV-A1"]["receipt_ids"] == ["GR-A1", "GR-A2"]
    assert pairs["IV-A1"]["approval"] == "KEPT"
    assert {item["invoice_id"] for item in stable["pending"]} >= {"IV-A2"}


# 默认策略：挪配让两张都结算，且已核准发票不会被挤成挂起。
def test_default_policy_prefers_settlement_over_stability():
    result = solve_request(normalize_request(demo_request()))
    pairs = by_invoice(result)
    assert pairs["IV-A1"]["approval"] == "MOVED"
    assert pairs["IV-A2"]["approval"] == "NEW"
    assert result["totals"]["settled_invoices"] == 3


# 同一张验收单绝不能算给两张发票。
def test_receipt_is_never_shared_between_invoices():
    result = allocate(
        receipts(("GR-1", "A", "0.13", "500")),
        invoices(("IV-1", "A", "0.13", "500"), ("IV-2", "A", "0.13", "500")),
    )
    usage = receipt_usage(result["pairings"])
    assert usage["rows"] == usage["distinct"] and usage["unique"]
    assert len(result["pairings"]) == 1 and len(result["pending"]) == 1


# 不同税率即使货号相同也不能互相组合。
def test_tax_groups_never_offset():
    result = allocate(
        receipts(("GR-1", "A", "0.13", "100")),
        invoices(("IV-1", "A", "0.09", "100")),
    )
    assert not result["pairings"]
    assert result["pending"][0]["reason_code"] == "NO_RECEIPT"


# 输入顺序变化不改变配对结果。
def test_allocation_is_order_independent():
    payload = demo_request()
    shuffled = copy.deepcopy(payload)
    shuffled["receipts"].reverse()
    shuffled["invoices"].reverse()
    shuffled["approved"].reverse()
    left = solve_request(normalize_request(payload))
    right = solve_request(normalize_request(shuffled))
    assert [(item["invoice_id"], sorted(item["receipt_ids"]))
            for item in left["pairings"]] == \
           [(item["invoice_id"], sorted(item["receipt_ids"]))
            for item in right["pairings"]]


# 差异批次按实际配对金额对账，而不是发票面值。
def test_reconcile_uses_actual_pairing_amounts():
    result = allocate(
        receipts(("GR-1", "A", "0.13", "100.00")),
        invoices(("IV-1", "A", "0.13", "100.00")),
    )
    compare = reconcile_result(result)
    assert compare["balanced"]
    assert compare["matched_receipt_amount"] == "100.00"
    assert compare["matched_invoice_amount"] == "100.00"
    assert compare["receipt_usage"]["distinct"] == 1


# M1 采信结果决定匹配边界：被隔离的行不进入候选。
def test_quarantined_document_never_enters_matching():
    payload = {
        "documents": [
            {"doc_type": "GR", "doc_no": "GR-1", "sku": "SKU-A", "qty": "1",
             "amount": "500.00", "tax_rate": "13%"},
            {"doc_type": "GR", "doc_no": "GR-2", "sku": "SKU-A", "qty": "1",
             "amount": "", "tax_rate": "13%"},
            {"doc_type": "IV", "doc_no": "IV-1", "sku": "SKU-A", "qty": "1",
             "amount": "1000.00", "tax_rate": "13%"},
        ],
    }
    result = solve_request(normalize_request(payload))
    assert len(result["quarantined"]) == 1
    assert result["quarantined"][0]["reason_code"] == "EMPTY_VALUE"
    assert not result["pairings"]
    assert result["pending"][0]["reason_code"] == "NO_EQUAL_SUBSET"


# 间隔行号：CSV 数据从物理第 2 行开始，隔离记录保留原始行号。
def test_csv_rows_keep_physical_line_numbers():
    payload = {"documents": [
        {"doc_type": "GR", "doc_no": "GR-1", "sku": "SKU-A", "qty": "1",
         "amount": "500.00", "tax_rate": "13%"},
        {"doc_type": "GR", "doc_no": "GR-2", "sku": "SKU-A", "qty": "1",
         "amount": "N/A", "tax_rate": "13%"},
    ]}
    normalized = normalize_request(payload)
    assert normalized["quarantined"][0]["line_no"] == 3
    assert normalized["receipts"][0]["line_no"] == 2


# 同批重跑复用批次，不重复占用验收单。
def test_batch_rerun_reuses_allocations(tmp_path, monkeypatch):
    import app.store as store
    from app.modules.m6_batches import service as batches

    monkeypatch.setattr(store, "DATABASE_PATH", tmp_path / "runs.sqlite3")
    payload = {
        "receipts": [{"id": "GR-1", "sku": "SKU-A", "tax_rate": "0.13", "amount": "500.00"}],
        "invoices": [{"id": "IV-1", "sku": "SKU-A", "tax_rate": "0.13", "amount": "500.00"}],
    }
    first = batches.run(payload)
    second = batches.run(payload)
    assert first["created"] and first["run_id"] == second["run_id"]
    assert second["reused"] and second["storage"]["receipt_usage"] == {"rows": 1, "distinct": 1}
    assert store.run_count() == 1


# 同一张验收单不能同时出现在两个批次的配对里（不同输入各自独立）。
def test_reconcile_exports_pending_reasons():
    from app.modules.m6_batches.exporter import differences_csv

    payload = {
        "receipts": [{"id": "GR-1", "sku": "SKU-A", "tax_rate": "0.13", "amount": "500.00"}],
        "invoices": [
            {"id": "IV-1", "sku": "SKU-A", "tax_rate": "0.13", "amount": "500.00"},
            {"id": "IV-2", "sku": "SKU-A", "tax_rate": "0.13", "amount": "500.00"},
        ],
    }
    result = solve_request(normalize_request(payload))
    compare = reconcile_result(result)
    text = differences_csv(compare["lines"])
    assert "RECEIPT_OCCUPIED" in text and "MATCHED" in text
