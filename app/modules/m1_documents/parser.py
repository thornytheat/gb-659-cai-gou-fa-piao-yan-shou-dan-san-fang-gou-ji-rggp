REQUIRED = ("doc_type", "doc_no", "sku", "qty", "amount", "tax_rate")

def parse_documents(rows: list[dict], start_line: int = 2) -> tuple[list[dict], list[dict]]:
    """按物理行采信单据；CSV 数据从第 2 行开始，内存载荷从第 1 行开始。"""
    accepted, bad, seen = [], [], {}
    for line_no, row in enumerate(rows, start_line):
        missing = [key for key in REQUIRED if not str(row.get(key, "")).strip()]
        if missing:
            bad.append({"line_no": line_no, "reason_code": "EMPTY_VALUE", "raw": row})
            continue
        key = (row["doc_type"], row["doc_no"], row["sku"], row["tax_rate"])
        if key in seen:
            code = "DUPLICATE_KEY" if seen[key] == row else "VALUE_CONFLICT"
            bad.append({"line_no": line_no, "reason_code": code, "raw": row})
            continue
        seen[key] = dict(row)
        accepted.append({**row, "line_no": line_no})
    return accepted, bad

def complete_tail(text: str) -> bool:
    """输入尾部必须落在记录边界上：未换行的残行、未闭合的引号字段都算截断。"""
    if not text:
        return True
    if not text.endswith("\n"):
        return False
    return text.count('"') % 2 == 0

