import json
import sqlite3
from datetime import datetime, timedelta, timezone

from app.config import DATABASE_PATH

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, input_sha256 TEXT NOT NULL, accepted INTEGER NOT NULL, quarantined INTEGER NOT NULL)",
    """CREATE TABLE IF NOT EXISTS batches (
        run_id TEXT PRIMARY KEY,
        input_sha256 TEXT NOT NULL,
        rule_version TEXT NOT NULL,
        accepted INTEGER NOT NULL,
        quarantined INTEGER NOT NULL,
        summary TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS batch_pairings (
        run_id TEXT NOT NULL,
        invoice_id TEXT NOT NULL,
        receipt_ids TEXT NOT NULL,
        invoice_amount TEXT NOT NULL,
        receipt_amount TEXT NOT NULL,
        approval TEXT NOT NULL,
        PRIMARY KEY (run_id, invoice_id)
    )""",
    """CREATE TABLE IF NOT EXISTS batch_receipt_usage (
        run_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        invoice_id TEXT NOT NULL,
        PRIMARY KEY (run_id, receipt_id)
    )""",
    """CREATE TABLE IF NOT EXISTS batch_pending (
        run_id TEXT NOT NULL,
        invoice_id TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        amount TEXT NOT NULL,
        PRIMARY KEY (run_id, invoice_id)
    )""",
)


def connect():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DATABASE_PATH)


def init_db():
    with connect() as db:
        for statement in SCHEMA:
            db.execute(statement)


def record_run(manifest: dict) -> bool:
    init_db()
    with connect() as db:
        before = db.total_changes
        db.execute("INSERT OR IGNORE INTO runs VALUES (?, ?, ?, ?)",
                   (manifest["run_id"], manifest["input_sha256"],
                    manifest["accepted"], manifest["quarantined"]))
        return db.total_changes > before


def run_count() -> int:
    init_db()
    with connect() as db:
        return db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


def local_timestamp() -> str:
    now = datetime.now(timezone(timedelta(hours=8)))
    return now.isoformat(timespec="seconds")


def save_batch(manifest: dict, result: dict) -> bool:
    """保存批次结果；同一 run_id 重跑只复用、不重复占用验收单。"""
    init_db()
    with connect() as db:
        existing = db.execute("SELECT 1 FROM batches WHERE run_id = ?",
                              (manifest["run_id"],)).fetchone()
        if existing:
            return False
        db.execute("INSERT INTO runs VALUES (?, ?, ?, ?)",
                   (manifest["run_id"], manifest["input_sha256"],
                    manifest["accepted"], manifest["quarantined"]))
        db.execute(
            "INSERT INTO batches VALUES (?, ?, ?, ?, ?, ?, ?)",
            (manifest["run_id"], manifest["input_sha256"], manifest["rule_version"],
             manifest["accepted"], manifest["quarantined"],
             json.dumps(result, ensure_ascii=False, sort_keys=True),
             local_timestamp()),
        )
        for pairing in result.get("pairings", []):
            db.execute(
                "INSERT INTO batch_pairings VALUES (?, ?, ?, ?, ?, ?)",
                (manifest["run_id"], pairing["invoice_id"],
                 json.dumps(pairing["receipt_ids"], ensure_ascii=False),
                 pairing["amount"], pairing["receipt_amount"], pairing["approval"]),
            )
            for receipt_id in pairing["receipt_ids"]:
                db.execute(
                    "INSERT INTO batch_receipt_usage VALUES (?, ?, ?)",
                    (manifest["run_id"], receipt_id, pairing["invoice_id"]),
                )
        for item in result.get("pending", []):
            db.execute(
                "INSERT INTO batch_pending VALUES (?, ?, ?, ?)",
                (manifest["run_id"], item["invoice_id"], item["reason_code"],
                 item["amount"]),
            )
        return True


def load_batch(run_id: str) -> dict | None:
    init_db()
    with connect() as db:
        row = db.execute("SELECT summary, created_at FROM batches WHERE run_id = ?",
                         (run_id,)).fetchone()
    if not row:
        return None
    summary = json.loads(row[0])
    summary["stored_at"] = row[1]
    return summary


def batch_summary(run_id: str) -> dict:
    init_db()
    with connect() as db:
        pairings = db.execute(
            "SELECT invoice_id, receipt_ids, invoice_amount, receipt_amount, approval "
            "FROM batch_pairings WHERE run_id = ? ORDER BY invoice_id", (run_id,),
        ).fetchall()
        pending = db.execute(
            "SELECT invoice_id, reason_code, amount FROM batch_pending "
            "WHERE run_id = ? ORDER BY invoice_id", (run_id,),
        ).fetchall()
        receipt_usage = db.execute(
            "SELECT COUNT(*), COUNT(DISTINCT receipt_id) FROM batch_receipt_usage "
            "WHERE run_id = ?", (run_id,),
        ).fetchone()
    return {
        "pairings": [
            {"invoice_id": item[0], "receipt_ids": json.loads(item[1]),
             "amount": item[2], "receipt_amount": item[3], "approval": item[4]}
            for item in pairings
        ],
        "pending": [
            {"invoice_id": item[0], "reason_code": item[1], "amount": item[2]}
            for item in pending
        ],
        "receipt_usage": {"rows": receipt_usage[0], "distinct": receipt_usage[1]},
    }


def reset_batches() -> None:
    init_db()
    with connect() as db:
        for table in ("batch_pairings", "batch_receipt_usage", "batch_pending",
                      "batches", "runs"):
            db.execute(f"DELETE FROM {table}")
