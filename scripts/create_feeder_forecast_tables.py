"""One-time (and safely re-runnable) setup: creates the tables used by
the forecast-based outage loss / daily feeder energy feature (feeder
identity registry, daily workbook uploads, per-slot forecast/meter data).

Uses this app's own utils.db.get_engine() -- no separate connection
string. Every statement is CREATE ... IF NOT EXISTS, so re-running this
is a no-op against an already-set-up database and never touches any
existing (non feeder_forecast) table.

Run from the outage_tracker-main/ directory:
    python scripts/create_feeder_forecast_tables.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.db import get_engine
from sqlalchemy import text

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "feeder_forecast" / "sql" / "feeder_forecast_tables.sql"

NEW_TABLES = [
    "feeder", "feeder_alias", "daily_workbook_upload", "daily_workbook_issues",
    "feeder_upload_staging", "feeder_daily", "feeder_hourly_forecast", "feeder_meter_reading",
]


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
            "WHERE table_schema = 'public' AND table_name = ANY(:names) ORDER BY table_name"
        ), {"names": NEW_TABLES}).fetchall()
    found = {t[0] for t in tables}
    print("tables created:", sorted(found))
    missing = set(NEW_TABLES) - found
    if missing:
        print("MISSING (did not get created):", sorted(missing))


if __name__ == "__main__":
    main()
