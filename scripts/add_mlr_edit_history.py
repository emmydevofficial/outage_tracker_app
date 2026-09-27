"""One-time (and safely re-runnable) migration: adds the report-edit-history
columns/table used by the chat assistant + editable sections feature
(views/monthly_load_review.py, monthly_report/chat.py).

A separate file from scripts/create_monthly_review_tables.py on purpose --
that one's schema file is already applied, and this migration is additive
only (ALTER ... ADD COLUMN IF NOT EXISTS, CREATE TABLE IF NOT EXISTS), so
re-running either script is a no-op against an already-migrated database.

Run from the outage_tracker-main/ directory:
    python scripts/add_mlr_edit_history.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.db import get_engine
from sqlalchemy import text

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "sql" / "monthly_review_edit_history.sql"


def _split_statements(sql: str) -> list[str]:
    stmts = []
    for raw in sql.split(";"):
        lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("--")]
        stmt = "\n".join(lines).strip()
        if stmt:
            stmts.append(stmt)
    return stmts


def main():
    sql = SCHEMA_FILE.read_text()
    statements = _split_statements(sql)
    engine = get_engine()
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    print(f"Ran {len(statements)} statement(s) from {SCHEMA_FILE.name}.")

    with engine.connect() as conn:
        cols = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'mlr_report' AND column_name IN ('original_text', 'final_text')"
        )).fetchall()
        exists = conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_name = 'mlr_report_edit'"
        )).fetchall()
    print("mlr_report new columns:", [c[0] for c in cols])
    print("mlr_report_edit table exists:", bool(exists))


if __name__ == "__main__":
    main()
