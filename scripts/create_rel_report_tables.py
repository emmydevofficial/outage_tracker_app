"""One-time (and safely re-runnable) setup: creates the rel_* tables used
by the SLA Outage Reports feature (views/sla_outage_reports.py).

Uses this app's own utils.db.get_engine() -- no separate connection
string. Every statement is CREATE ... IF NOT EXISTS, so re-running this is
a no-op against an already-set-up database and never touches any existing
(non rel_*) table.

Run from the outage_tracker-main/ directory:
    python scripts/create_rel_report_tables.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.db import get_engine
from sqlalchemy import text

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "reliability_report" / "sql" / "rel_report_tables.sql"


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
        tables = conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name LIKE 'rel_%' ORDER BY table_name"
        )).fetchall()
    print("rel_ tables:", [t[0] for t in tables])


if __name__ == "__main__":
    main()
