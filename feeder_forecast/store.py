"""Saves a parsed daily workbook (feeder_forecast/parser.py's ParseResult)
to the database. One transaction per file, per the spec ("each file is
validated and saved in its own transaction, so one bad file does not
stop the others") -- and every insert is batched (SQLAlchemy Core
insert() with a list of dicts), not one row at a time, per the lesson
from phase 1's seed script and this phase's own first parser timing
(116s row-by-row vs 5.65s batched/cached).

Re-uploading the same region+date replaces that region/date's rows
(delete-then-reinsert in the same transaction), matching
monthly_report/store.py's save_month() convention -- never duplicates.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import column, insert, table, text

from .parser import ParseResult

_DAILY_TBL = table("feeder_daily", *(column(c) for c in [
    "feeder_id", "reading_date", "region", "disco_seen", "feeder_band_seen", "tcn_limit_mw",
    "disco_base_load_mw", "disco_peak_load_mw", "opening_meter_mwh", "closing_meter_mwh",
    "daily_energy_mwh", "source_file", "station_raw", "feeder_raw", "upload_id", "uploaded_by"]))
_FORECAST_TBL = table("feeder_hourly_forecast", *(column(c) for c in [
    "feeder_id", "slot_date", "slot_time", "slot_start", "forecast_mw", "source_date", "source_label", "upload_id"]))
_READING_TBL = table("feeder_meter_reading", *(column(c) for c in [
    "feeder_id", "reading_date", "reading_time", "reading_at", "meter_reading_mwh", "actual_mwh",
    "source_date", "source_label", "flag", "upload_id"]))
_STAGING_TBL = table("feeder_upload_staging", *(column(c) for c in [
    "upload_id", "station_raw", "feeder_raw", "region", "row_data", "status"]))
_ISSUE_TBL = table("daily_workbook_issues", *(column(c) for c in [
    "upload_id", "rule_code", "sheet", "cell", "station_raw", "feeder_raw", "value_found", "expected", "action"]))


def save_upload(engine, result: ParseResult, file_name: str, user: str | None,
                 region_source: str = "sheet", date_source: str = "sheet",
                 confirmed_by: str | None = None, confirmation_note: str | None = None) -> int:
    """Saves result (status must be "ok", i.e. not a reject) and returns
    the new upload_id. Closing/daily-energy are filled in from the 24:00
    reading once all rows are known."""
    closing_by_feeder = {
        r["feeder_id"]: r["meter_reading_mwh"]
        for r in result.reading_rows if r["source_label"] == 24
    }
    daily_rows = []
    for d in result.daily_rows:
        closing = closing_by_feeder.get(d["feeder_id"])
        opening = d["opening_meter_mwh"]
        # any R32 flag that day makes the daily total unreliable too
        has_r32 = any(r["flag"] == "R32" for r in result.reading_rows if r["feeder_id"] == d["feeder_id"])
        daily_energy = (closing - opening) if (closing is not None and opening is not None and not has_r32) else None
        daily_rows.append(dict(d, closing_meter_mwh=closing, daily_energy_mwh=daily_energy))

    status = "saved_with_warnings" if result.issues else "saved"

    with engine.begin() as con:
        # replace this region+date's existing rows, if any (re-upload)
        old = con.execute(text(
            "SELECT upload_id FROM daily_workbook_upload WHERE resolved_region=:r AND resolved_date=:d "
            "AND status IN ('saved','saved_with_warnings') ORDER BY uploaded_at DESC LIMIT 1"
        ), dict(r=result.region, d=result.date)).fetchone()
        replaced_upload_id = old[0] if old else None
        if replaced_upload_id:
            # one statement, not a per-feeder loop -- feeder_daily's PK is
            # (feeder_id, reading_date), so region+date alone identifies
            # every row to replace without needing the feeder_id list at all.
            con.execute(text("DELETE FROM feeder_daily WHERE region=:r AND reading_date=:d"),
                       dict(r=result.region, d=result.date))
            con.execute(text("DELETE FROM feeder_hourly_forecast WHERE upload_id=:u"), dict(u=replaced_upload_id))
            con.execute(text("DELETE FROM feeder_meter_reading WHERE upload_id=:u"), dict(u=replaced_upload_id))
            con.execute(text("DELETE FROM feeder_upload_staging WHERE upload_id=:u"), dict(u=replaced_upload_id))

        upload_id = con.execute(text("""
            INSERT INTO daily_workbook_upload
                (file_name, resolved_region, resolved_date, region_source, date_source,
                 confirmed_by, confirmation_note, status, feeders_read, rows_saved, rows_skipped,
                 warnings, replaced_upload_id, uploaded_by)
            VALUES (:file_name, :region, :date, :region_source, :date_source,
                    :confirmed_by, :confirmation_note, :status, :feeders_read, :rows_saved, :rows_skipped,
                    :warnings, :replaced_upload_id, :uploaded_by)
            RETURNING upload_id
        """), dict(file_name=file_name, region=result.region, date=result.date,
                   region_source=region_source, date_source=date_source,
                   confirmed_by=confirmed_by, confirmation_note=confirmation_note, status=status,
                   feeders_read=result.feeders_read, rows_saved=result.rows_saved, rows_skipped=result.rows_skipped,
                   warnings=len(result.issues), replaced_upload_id=replaced_upload_id,
                   uploaded_by=user)).scalar_one()

        if result.issues:
            con.execute(insert(_ISSUE_TBL), [dict(i, upload_id=upload_id) for i in result.issues])
        if daily_rows:
            con.execute(insert(_DAILY_TBL), [dict(d, upload_id=upload_id, uploaded_by=user) for d in daily_rows])
        if result.forecast_rows:
            con.execute(insert(_FORECAST_TBL), [dict(r, upload_id=upload_id) for r in result.forecast_rows])
        if result.reading_rows:
            con.execute(insert(_READING_TBL), [dict(r, upload_id=upload_id) for r in result.reading_rows])
        if result.unmatched_rows:
            import json
            con.execute(insert(_STAGING_TBL), [
                dict(upload_id=upload_id, station_raw=u["station"], feeder_raw=u["feeder_name"],
                     region=u["region"], row_data=json.dumps(u, default=str), status="pending")
                for u in result.unmatched_rows
            ])

    return upload_id
