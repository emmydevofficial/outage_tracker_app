"""Save and load monthly returns in PostgreSQL (SQLAlchemy engine)."""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from sqlalchemy import column, insert, table, text

from .metrics import equipment_key

COLS = ["period", "region", "kind", "equipment_key", "sheet_row", "substation", "name", "nomenclature", "rating_mva",
        "max_mw", "max_amps", "max_kv", "max_time", "max_date", "temp_primary_max", "temp_secondary_max",
        "min_mw", "min_amps", "min_kv", "min_time", "min_date", "energy_reading", "status_text", "remarks"]

# Lightweight Core table constructs (not full ORM models) purely so
# save_month() can use sqlalchemy.insert() instead of a raw text(INSERT)
# string. This matters: SQLAlchemy 2.0's "insertmanyvalues" feature -- which
# batches a list-of-dicts insert into a handful of multi-row INSERT
# statements instead of one network round trip per row -- only kicks in for
# the Core insert() construct, not for text(). A 1,774-row month took several
# minutes over the remote connection with text(); this brings it back to a
# few seconds, with no behaviour change.
_READING_TBL = table("mlr_reading", column("batch_id"), *(column(c) for c in COLS))
_ISSUE_TBL = table("mlr_issue", column("batch_id"), column("kind"), column("sheet_row"),
                    column("field"), column("problem"), column("raw"))
_EDIT_TBL = table("mlr_report_edit", column("report_id"), column("section"), column("old_text"),
                   column("new_text"), column("source"), column("status"), column("reason"),
                   column("edited_by"), column("edited_at"))


def save_month(engine, period: dt.date, rows: list[dict], issues: list[dict], files: list[dict], user: str | None) -> None:
    """Replace each uploaded region's data for the month in one transaction."""
    with engine.begin() as con:
        for f in files:
            region = f["region"]
            con.execute(text("DELETE FROM mlr_upload_batch WHERE period=:p AND region=:r"), dict(p=period, r=region))
            reg_rows = [r for r in rows if r["region"] == region]
            reg_issues = [i for i in issues if i["region"] == region]
            batch_id = con.execute(text(
                "INSERT INTO mlr_upload_batch (period, region, file_name, file_sha256, uploaded_by, row_count, issue_count) "
                "VALUES (:p,:r,:fn,:sha,:u,:rc,:ic) RETURNING id"),
                dict(p=period, r=region, fn=f["name"], sha=f["sha"], u=user, rc=len(reg_rows), ic=len(reg_issues))).scalar_one()
            if reg_rows:
                con.execute(insert(_READING_TBL),
                    [dict({c: r.get(c) for c in COLS}, batch_id=batch_id, period=period, equipment_key=equipment_key(r)) for r in reg_rows])
            if reg_issues:
                con.execute(insert(_ISSUE_TBL), [dict(i, batch_id=batch_id) for i in reg_issues])


def load_month(engine, period: dt.date) -> list[dict]:
    with engine.connect() as con:
        res = con.execute(text(f"SELECT {', '.join(c for c in COLS if c not in ('period', 'equipment_key'))} "
                               "FROM mlr_reading WHERE period=:p"), dict(p=period))
        return [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in r._mapping.items()} for r in res]


def meter_settings(engine) -> tuple[dict, dict]:
    """Returns (multipliers, price_per_kwh by equipment_key) with kWh meters converted to MWh."""
    mult, price = {}, {}
    with engine.connect() as con:
        for r in con.execute(text("""
            SELECT m.equipment_key, m.multiplier, m.unit, t.naira_per_kwh
            FROM mlr_meter m
            LEFT JOIN LATERAL (SELECT naira_per_kwh FROM mlr_tariff t WHERE t.band = m.tariff_band
                               ORDER BY effective_from DESC LIMIT 1) t ON true""")):
            mult[r.equipment_key] = float(r.multiplier) / (1000 if r.unit == "kWh" else 1)
            if r.naira_per_kwh is not None:
                price[r.equipment_key] = float(r.naira_per_kwh)
    return mult, price


def save_report(engine, period, html, facts, narrative_by, notes, user, prompt_version="v1",
                 original_text=None, final_text=None) -> int:
    with engine.begin() as con:
        return con.execute(text(
            "INSERT INTO mlr_report (period, created_by, narrative_by, prompt_version, facts, html, notes, "
            "original_text, final_text) "
            "VALUES (:p,:u,:n,:v,CAST(:f AS JSONB),:h,CAST(:no AS JSONB),CAST(:ot AS JSONB),CAST(:ft AS JSONB)) "
            "RETURNING id"),
            dict(p=period, u=user, n=narrative_by, v=prompt_version, f=json.dumps(facts, default=str), h=html,
                 no=json.dumps(notes), ot=json.dumps(original_text, default=str) if original_text else None,
                 ft=json.dumps(final_text, default=str) if final_text else None)).scalar_one()


def save_report_edits(engine, report_id: int, edits: list[dict]) -> None:
    """Bulk-insert the draft's edit log (manual, reset and chat entries,
    including rejected chat proposals) -- Core insert() for the same
    batching reason as save_month()'s reading insert, not that this list
    is ever large enough to matter much."""
    if not edits:
        return
    with engine.begin() as con:
        con.execute(insert(_EDIT_TBL), [
            dict(report_id=report_id, section=e["section"], old_text=e["old"], new_text=e["new"],
                 source=e["source"], status=e.get("status", "accepted"), reason=e.get("reason"),
                 edited_by=e.get("by"), edited_at=e.get("at"))
            for e in edits
        ])


def list_report_edits(engine, report_id: int) -> list[dict]:
    with engine.connect() as con:
        return [dict(r._mapping) for r in con.execute(text(
            "SELECT section, source, status, reason, edited_by, edited_at FROM mlr_report_edit "
            "WHERE report_id=:i ORDER BY edited_at"), dict(i=report_id))]


def list_reports(engine) -> list[dict]:
    with engine.connect() as con:
        return [dict(r._mapping) for r in con.execute(text(
            "SELECT id, period, created_at, created_by, narrative_by FROM mlr_report ORDER BY created_at DESC LIMIT 50"))]


def get_report_html(engine, report_id: int) -> str:
    with engine.connect() as con:
        return con.execute(text("SELECT html FROM mlr_report WHERE id=:i"), dict(i=report_id)).scalar_one()
