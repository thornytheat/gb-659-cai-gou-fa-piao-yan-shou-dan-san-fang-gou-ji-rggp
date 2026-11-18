"""发票与验收单的稳定分配。

规则要点：

* 只在同一 ``(sku, tax_rate)`` 组内组合，不同税率绝不互相抵消。
* 一张验收单最多归属一张发票（集合互斥）。
* 已核准配对优先保持原样；只有在能多结算发票时，才允许把已核准
  发票挪到另一组等额验收单上（挪配后两张发票都能结算）。
* 结算数量相同的方案之间，改动已核准配对的方案不会被选中。
* 组合顺序不影响结果：候选组合与求解顺序都按编号稳定排序。
"""

from decimal import Decimal, ROUND_HALF_UP
from itertools import combinations

CENT = Decimal("0.01")
DEFAULT_TOLERANCE = Decimal("0.01")
DEFAULT_LIMIT = 4
MAX_CANDIDATES = 64
MAX_NODES = 40_000


def money_text(value) -> str:
    """金额统一按分、半入后输出为字符串。"""
    return str(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP))


def normalize_tax_rate(value) -> Decimal:
    """同时接受 ``13%``、``13`` 与 ``0.13`` 三种写法。"""
    token = str(value).strip().replace("%", "").replace("，", ",")
    rate = Decimal(token)
    return rate / 100 if rate > 1 else rate


def business_key(sku: str, tax_rate) -> str:
    return f"{sku}|{normalize_tax_rate(tax_rate)}"


def candidate_subsets(receipts: list[dict], target: Decimal,
                      tolerance: Decimal = DEFAULT_TOLERANCE,
                      limit: int = DEFAULT_LIMIT) -> list[tuple[dict, ...]]:
    """枚举金额落入容差范围的验收单组合，按组合大小与编号稳定排序。

    使用按大小分层的子集和搜索：金额非负，部分和超过上界即可停止，
    剩余金额不足以补齐下界时也可停止，避免真实数据上组合爆炸。
    """
    # 搜索按金额从大到小，配合后缀和剪枝：一旦“当前和 + 剩余全部”仍低于
    # 下界，后续更小的金额都不可能成功，可以整段停止。
    search_order = sorted(receipts, key=lambda row: (-_amount(row), str(row["id"])))
    ordered = search_order
    amounts = [_amount(row) for row in ordered]
    if any(amount < 0 for amount in amounts):
        # 负数会让“部分和越界即停”的剪枝失效，退回完整枚举。
        found_all: list[tuple[dict, ...]] = []
        for size in range(1, min(limit, len(ordered)) + 1):
            for group in combinations(ordered, size):
                total = sum((_amount(row) for row in group), Decimal("0"))
                if abs(total - target) <= tolerance:
                    found_all.append(group)
                    if len(found_all) >= MAX_CANDIDATES:
                        break
            if len(found_all) >= MAX_CANDIDATES:
                break
        found_all.sort(key=_subset_sort_key)
        return found_all

    suffix = [Decimal("0")] * (len(ordered) + 1)
    for index in range(len(ordered) - 1, -1, -1):
        suffix[index] = suffix[index + 1] + amounts[index]
    lower = target - tolerance
    upper = target + tolerance
    found: list[tuple[dict, ...]] = []

    def walk(start: int, chosen: list[dict], total: Decimal, size: int) -> None:
        if len(chosen) == size:
            if lower <= total <= upper:
                found.append(tuple(chosen))
            return
        remaining_needed = size - len(chosen)
        for position in range(start, len(ordered)):
            if len(ordered) - position < remaining_needed:
                break
            amount = amounts[position]
            if total + amount > upper:
                # 金额已按降序排列，之后更小的金额仍可能补齐，不能整段跳过。
                continue
            if total + amount + suffix[position + 1] < lower:
                # 后缀是往后所有金额的上界；它都不够，后面更不可能。
                break
            chosen.append(ordered[position])
            walk(position + 1, chosen, total + amount, size)
            chosen.pop()
            if len(found) >= MAX_CANDIDATES:
                return

    for size in range(1, min(limit, len(ordered)) + 1):
        walk(0, [], Decimal("0"), size)
        if len(found) >= MAX_CANDIDATES:
            break
    found.sort(key=_subset_sort_key)
    return found


def _subset_sort_key(group: tuple[dict, ...]):
    return (
        len(group),
        tuple(str(row["id"]) for row in group),
        tuple(money_text(_amount(row)) for row in group),
    )


def _amount(row: dict) -> Decimal:
    return Decimal(str(row["amount"]))


def _approved_map(approved: list[dict] | None) -> dict[str, frozenset[str]]:
    result = {}
    for item in approved or []:
        invoice_id = str(item.get("invoice_id") or item.get("invoice"))
        receipt_ids = item.get("receipt_ids") or item.get("receipts") or []
        result[invoice_id] = frozenset(str(receipt_id) for receipt_id in receipt_ids)
    return result


def _solve_group(receipts: list[dict], invoices: list[dict],
                 approved_map: dict[str, frozenset[str]],
                 tolerance: Decimal, limit: int,
                 policy: str = "settle") -> tuple[dict[str, tuple[str, ...]], dict[str, list[tuple[dict, ...]]]]:
    candidate_map = {
        invoice["id"]: candidate_subsets(receipts, _amount(invoice), tolerance, limit)
        for invoice in invoices
    }
    # 已核准配对本身一定是候选，且不受组合上限限制——否则一张已核准的
    # 多张验收单会因 limit 被无声丢弃，稳定性就无从谈起。
    receipt_by_id = {row["id"]: row for row in receipts}
    for invoice in invoices:
        approved = approved_map.get(invoice["id"])
        if not approved:
            continue
        if any(receipt_id not in receipt_by_id for receipt_id in approved):
            continue
        total = sum((_amount(receipt_by_id[receipt_id]) for receipt_id in approved),
                    Decimal("0"))
        if abs(total - _amount(invoice)) > tolerance:
            continue
        approved_subset = tuple(receipt_by_id[receipt_id] for receipt_id in sorted(approved))
        existing = candidate_map[invoice["id"]]
        if not any(frozenset(row["id"] for row in subset) == approved
                   for subset in existing):
            candidate_map[invoice["id"]] = [approved_subset] + existing
    # ``stable`` 把已核准配对当作硬约束；``settle``（默认）优先多结算，
    # 结算数量相同时优先保留已核准配对。
    if policy == "stable":
        for invoice in invoices:
            approved = approved_map.get(invoice["id"])
            if approved is not None:
                candidate_map[invoice["id"]] = [
                    subset for subset in candidate_map[invoice["id"]]
                    if frozenset(row["id"] for row in subset) == approved
                ]
    # 可选组合越少越先处理，尽早收紧搜索边界。
    order = sorted(invoices, key=lambda invoice: (len(candidate_map[invoice["id"]]), invoice["id"]))
    approved_ids = {
        invoice["id"] for invoice in invoices if invoice["id"] in approved_map
    }
    remaining_approved = [0] * (len(order) + 1)
    for index in range(len(order) - 1, -1, -1):
        remaining_approved[index] = remaining_approved[index + 1] + (
            1 if order[index]["id"] in approved_ids else 0
        )
    # 理论最优（按字典序）：已核准全结算、发票全结算、核准配对全保留。
    ceiling = (len(approved_ids), len(order), len(approved_ids))
    best = {"key": None, "canonical": None, "assignment": {}}
    nodes = 0

    def record(assignment: dict[str, tuple[str, ...]]) -> None:
        approved_settled = sum(1 for invoice_id in assignment if invoice_id in approved_ids)
        stability = sum(
            1 for invoice_id, receipt_ids in assignment.items()
            if approved_map.get(invoice_id) == frozenset(receipt_ids)
        )
        key = (approved_settled, len(assignment), stability)
        canonical = tuple(sorted(
            (invoice_id, tuple(sorted(receipt_ids)))
            for invoice_id, receipt_ids in assignment.items()
        ))
        if best["key"] is None or key > best["key"] or (
            key == best["key"] and canonical < best["canonical"]
        ):
            best.update(key=key, canonical=canonical, assignment=dict(assignment))

    def visit(index: int, used: frozenset[str], assignment: dict[str, tuple[str, ...]]) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES:
            return
        approved_here = sum(1 for invoice_id in assignment if invoice_id in approved_ids)
        stability_here = sum(
            1 for invoice_id, receipt_ids in assignment.items()
            if approved_map.get(invoice_id) == frozenset(receipt_ids)
        )
        # 字典序上界：剩余每张已核准发票最多再贡献一个结算与一次保留。
        bound = (
            approved_here + remaining_approved[index],
            len(assignment) + (len(order) - index),
            stability_here + remaining_approved[index],
        )
        if best["key"] is not None and bound < best["key"]:
            return
        if index == len(order):
            record(assignment)
            return
        invoice = order[index]
        for subset in candidate_map[invoice["id"]]:
            receipt_ids = tuple(row["id"] for row in subset)
            if any(receipt_id in used for receipt_id in receipt_ids):
                continue
            assignment[invoice["id"]] = receipt_ids
            visit(index + 1, used | frozenset(receipt_ids), assignment)
            del assignment[invoice["id"]]
            if best["key"] == ceiling:
                return
        # 当前发票无法再贡献更优结果时仍需继续处理后面的发票。
        visit(index + 1, used, assignment)

    visit(0, frozenset(), {})
    if best["key"] is None:
        record({})
    return best["assignment"], candidate_map


def _pending_reason(invoice_id: str, receipt_by_id: dict[str, dict],
                    candidates: list[tuple[dict, ...]],
                    owner: dict[str, str], kept_approved: set[str],
                    approved_map: dict[str, frozenset[str]]) -> str:
    approved = approved_map.get(invoice_id)
    if approved and any(receipt_id not in receipt_by_id for receipt_id in approved):
        return "APPROVED_RECEIPT_MISSING"
    if not receipt_by_id:
        return "NO_RECEIPT"
    if not candidates:
        return "NO_EQUAL_SUBSET"
    blocking_owners = set()
    for subset in candidates:
        blocking_owners.update(
            owner[row["id"]] for row in subset if row["id"] in owner
        )
    if blocking_owners and blocking_owners.issubset(kept_approved):
        return "RECEIPT_HELD_BY_APPROVED"
    return "RECEIPT_OCCUPIED"


def allocate(receipts: list[dict], invoices: list[dict],
             approved: list[dict] | None = None,
             tolerance: Decimal | str = DEFAULT_TOLERANCE,
             limit: int = DEFAULT_LIMIT,
             policy: str = "settle") -> dict:
    """把验收单分配给发票，返回配对、待处理与金额合计。

    ``policy="settle"``：已核准配对保持稳定，但若挪配能让更多发票结算则挪配。
    ``policy="stable"``：已核准配对视为硬约束，新发票只能使用剩余验收单。
    """
    tolerance = Decimal(str(tolerance))
    if policy not in ("settle", "stable"):
        raise ValueError("UNKNOWN_POLICY")
    approved_map = _approved_map(approved)
    groups: dict[tuple[str, Decimal], dict[str, list[dict]]] = {}
    rejected: list[dict] = []
    usable_receipts: list[dict] = []

    for row in receipts:
        try:
            key = (str(row["sku"]), normalize_tax_rate(row["tax_rate"]))
            amount = _amount(row)
        except (KeyError, ValueError, ArithmeticError):
            rejected.append({"document_id": str(row.get("id")), "doc_type": "GR",
                             "reason_code": "BAD_FORMAT"})
            continue
        groups.setdefault(key, {"receipts": [], "invoices": []})["receipts"].append(
            {**row, "amount": amount}
        )
        usable_receipts.append({**row, "amount": amount})

    for invoice in invoices:
        try:
            key = (str(invoice["sku"]), normalize_tax_rate(invoice["tax_rate"]))
            amount = _amount(invoice)
        except (KeyError, ValueError, ArithmeticError):
            rejected.append({"document_id": str(invoice.get("id")), "doc_type": "IV",
                             "reason_code": "BAD_FORMAT"})
            continue
        groups.setdefault(key, {"receipts": [], "invoices": []})["invoices"].append(
            {**invoice, "amount": amount}
        )

    pairings: list[dict] = []
    pending: list[dict] = []
    used: set[str] = set()

    for key in sorted(groups, key=lambda item: (item[0], str(item[1]))):
        group = groups[key]
        receipt_by_id = {row["id"]: row for row in group["receipts"]}
        assignment, candidate_map = _solve_group(
            group["receipts"], group["invoices"], approved_map, tolerance, limit, policy
        )
        owner = {
            receipt_id: invoice_id
            for invoice_id, receipt_ids in assignment.items()
            for receipt_id in receipt_ids
        }
        used.update(owner)
        kept_approved = {
            invoice_id for invoice_id, receipt_ids in assignment.items()
            if approved_map.get(invoice_id) == frozenset(receipt_ids)
        }

        for invoice in sorted(group["invoices"], key=lambda row: row["id"]):
            invoice_id = invoice["id"]
            if invoice_id in assignment:
                receipt_ids = tuple(assignment[invoice_id])
                receipt_amount = sum(
                    (_amount(receipt_by_id[receipt_id]) for receipt_id in receipt_ids),
                    Decimal("0"),
                )
                previous = approved_map.get(invoice_id)
                if previous is None:
                    approval = "NEW"
                elif previous == frozenset(receipt_ids):
                    approval = "KEPT"
                else:
                    approval = "MOVED"
                pairings.append({
                    "invoice_id": invoice_id,
                    "invoice_no": invoice.get("doc_no", invoice_id),
                    "receipt_ids": list(receipt_ids),
                    "amount": money_text(invoice["amount"]),
                    "receipt_amount": money_text(receipt_amount),
                    "difference": money_text(receipt_amount - invoice["amount"]),
                    "business_key": business_key(invoice["sku"], invoice["tax_rate"]),
                    "line_no": invoice.get("line_no"),
                    "approval": approval,
                    "previous_receipt_ids": sorted(previous) if previous is not None else [],
                })
            else:
                candidates = candidate_map[invoice_id]
                reason_code = _pending_reason(
                    invoice_id, receipt_by_id, candidates,
                    owner, kept_approved, approved_map,
                )
                pending.append({
                    "invoice_id": invoice_id,
                    "invoice_no": invoice.get("doc_no", invoice_id),
                    "amount": money_text(invoice["amount"]),
                    "business_key": business_key(invoice["sku"], invoice["tax_rate"]),
                    "line_no": invoice.get("line_no"),
                    "reason_code": reason_code,
                    "candidate_receipt_ids": [
                        [row["id"] for row in subset] for subset in candidates
                    ],
                    "previous_receipt_ids": sorted(approved_map.get(invoice_id, ())),
                })

    uninvoiced = []
    for receipt in sorted(usable_receipts, key=lambda row: str(row["id"])):
        if receipt["id"] in used:
            continue
        uninvoiced.append({
            "receipt_id": receipt["id"],
            "business_key": business_key(receipt["sku"], receipt["tax_rate"]),
            "line_no": receipt.get("line_no"),
            "amount": money_text(receipt["amount"]),
            "reason_code": "UNINVOICED",
        })

    matched_invoice_amount = sum(
        (Decimal(pairing["amount"]) for pairing in pairings), Decimal("0")
    )
    matched_receipt_amount = sum(
        (Decimal(pairing["receipt_amount"]) for pairing in pairings), Decimal("0")
    )
    accepted_receipt_amount = sum((_amount(row) for row in receipts), Decimal("0"))
    accepted_invoice_amount = sum((_amount(row) for row in invoices), Decimal("0"))
    uninvoiced_amount = sum((Decimal(row["amount"]) for row in uninvoiced), Decimal("0"))

    return {
        "pairings": pairings,
        "pending": pending,
        "uninvoiced": uninvoiced,
        "rejected": rejected,
        "totals": {
            "accepted_receipt_amount": money_text(accepted_receipt_amount),
            "accepted_invoice_amount": money_text(accepted_invoice_amount),
            "matched_invoice_amount": money_text(matched_invoice_amount),
            "matched_receipt_amount": money_text(matched_receipt_amount),
            "difference": money_text(matched_receipt_amount - matched_invoice_amount),
            "uninvoiced_amount": money_text(uninvoiced_amount),
            "pending_invoice_amount": money_text(
                accepted_invoice_amount - matched_invoice_amount
            ),
            "settled_invoices": len(pairings),
            "pending_invoices": len(pending),
            "uninvoiced_receipts": len(uninvoiced),
        },
    }
