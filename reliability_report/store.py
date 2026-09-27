"""CRUD for rel_report_run / rel_allowance_override / rel_report_note."""
from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import text


def save_run(engine, period_type: str, period_start: dt.date, period_end: dt.date, period_label: str,
             scope: str, facts: dict, ai_text: dict, notes: list, model: str, user: str,
             docx: bytes, prompt_version: str = "v1") -> int:
    with engine.begin() as con:
        return con.execute(text(
            "INSERT INTO rel_report_run (period_type, period_start, period_end, period_label, scope, "
            "facts, ai_text, notes, model, prompt_version, docx, created_by) "
            "VALUES (:pt,:ps,:pe,:pl,:sc,CAST(:f AS JSONB),CAST(:at AS JSONB),CAST(:no AS JSONB),:m,:pv,:d,:u) "
            "RETURNING id"),
            dict(pt=period_type, ps=period_start, pe=period_end, pl=period_label, sc=scope,
                 f=json.dumps(facts, default=str), at=json.dumps(ai_text, default=str),
                 no=json.dumps(notes, default=str), m=model, pv=prompt_version, d=docx, u=user)
        ).scalar_one()


def mark_issued(engine, run_ids: list[int], user: str) -> None:
    if not run_ids:
        return
    with engine.begin() as con:
        con.execute(text(
            "UPDATE rel_report_run SET status='issued', issued_by=:u, issued_at=now() "
            "WHERE id = ANY(:ids)"), dict(u=user, ids=run_ids))


def list_runs(engine, period_type: str | None = None, limit: int = 100) -> list[dict]:
    q = "SELECT id, period_type, period_start, period_end, period_label, scope, status, model, created_by, created_at, issued_at FROM rel_report_run"
    params: dict = {"limit": limit}
    if period_type:
        q += " WHERE period_type=:pt"
        params["pt"] = period_type
    q += " ORDER BY period_start DESC, scope LIMIT :limit"
    with engine.connect() as con:
        return [dict(r._mapping) for r in con.execute(text(q), params)]


def get_issued_run(engine, period_type: str, period_start: dt.date, period_end: dt.date, scope: str) -> dict | None:
    """The most recent issued run for this exact period+scope -- what a
    later period's "compared with the previous issued period" figures are
    read from. Never a recalculation."""
    with engine.connect() as con:
        row = con.execute(text(
            "SELECT id, facts, ai_text FROM rel_report_run "
            "WHERE period_type=:pt AND period_start=:ps AND period_end=:pe AND scope=:sc AND status='issued' "
            "ORDER BY issued_at DESC LIMIT 1"),
            dict(pt=period_type, ps=period_start, pe=period_end, sc=scope)).fetchone()
    return dict(row._mapping) if row else None


def get_run_docx(engine, run_id: int) -> bytes:
    with engine.connect() as con:
        return con.execute(text("SELECT docx FROM rel_report_run WHERE id=:i"), dict(i=run_id)).scalar_one()


def add_override(engine, station: str, feeder: str, date_from: dt.date, date_to: dt.date,
                  allowed_hours_per_day: float | None, exclude_flag: bool, reason: str, user: str) -> int:
    with engine.begin() as con:
        return con.execute(text(
            "INSERT INTO rel_allowance_override (station, feeder, date_from, date_to, allowed_hours_per_day, "
            "exclude_flag, reason, entered_by) VALUES (:st,:f,:df,:dt,:h,:ex,:r,:u) RETURNING id"),
            dict(st=station, f=feeder, df=date_from, dt=date_to, h=allowed_hours_per_day,
                 ex=exclude_flag, r=reason, u=user)).scalar_one()


def list_overrides(engine, date_from: dt.date | None = None, date_to: dt.date | None = None) -> list[dict]:
    q = "SELECT * FROM rel_allowance_override"
    params = {}
    if date_from and date_to:
        q += " WHERE date_from <= :dt AND date_to >= :df"
        params = {"df": date_from, "dt": date_to}
    q += " ORDER BY entered_at DESC"
    with engine.connect() as con:
        return [dict(r._mapping) for r in con.execute(text(q), params)]


def add_note(engine, period_start: dt.date, period_end: dt.date, region: str | None,
             kind: str, text_: str, user: str) -> int:
    with engine.begin() as con:
        return con.execute(text(
            "INSERT INTO rel_report_note (period_start, period_end, region, kind, text, entered_by) "
            "VALUES (:ps,:pe,:r,:k,:t,:u) RETURNING id"),
            dict(ps=period_start, pe=period_end, r=region, k=kind, t=text_, u=user)).scalar_one()


def list_notes(engine, period_start: dt.date, period_end: dt.date, region: str | None = None) -> list[dict]:
    q = "SELECT * FROM rel_report_note WHERE period_start=:ps AND period_end=:pe"
    params = {"ps": period_start, "pe": period_end}
    if region:
        q += " AND (region=:r OR region IS NULL OR region='ALL')"
        params["r"] = region
    q += " ORDER BY entered_at"
    with engine.connect() as con:
        return [dict(r._mapping) for r in con.execute(text(q), params)]
