"""Parses a daily regional feeder workbook (one file per region per day)
into rows ready for feeder_daily / feeder_hourly_forecast /
feeder_meter_reading, per the rules in utils/daily_workbook_rules.py.

Three entry points:
  parse_filename(file_name)   -- best-effort region/date guess from the
                                  name alone, never authoritative on its own.
  parse_control_sheet(wb, file_name) -- resolves region+date per section 3a,
                                  returns resolved values, a pause, or a reject.
  parse_workbook(path, region, date, engine) -- the row/cell parse per
                                  section 3b, once region+date are settled.
  resolve_feeder(engine, region, station, feeder) -- feeder_alias lookup,
                                  reused at upload time and (later) for
                                  resolving outages rows to a feeder_id.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from sqlalchemy import text

from reliability_report.data import normalize_feeder, normalize_station
from utils.daily_workbook_rules import issue
from utils.regions import REGIONS

_REGION_NORM = {re.sub(r"[^A-Z]", "", r.upper()): r for r in REGIONS}

_DATE_8 = re.compile(r"(?<!\d)(\d{2})[-.]?(\d{2})[-.]?(\d{4})(?!\d)")
_DATE_6 = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)")

EXPECTED_HOUR_LABELS = [f"{h:02d}:00" for h in range(1, 25)]
FIXED_HEADERS = ["REGION", "DISCO", "AREA CONTROL", "STATION", "TRANSFORMER NAME", "RATING",
                  "FEEDER BAND", "ASSOCIATED 33KV FEEDER", "TCN Limits", "Disco Base Load",
                  "Disco Peak Load", "METER READING AT 00:00HRS MWH"]
BLOCK_HEADERS = ["FORECAST", "PRESENT METER READING MWH", "ACTUAL MW", "DIFFERENCE"]


def _match_region_token(token: str) -> str | None:
    if not token:
        return None
    if token in _REGION_NORM:
        return _REGION_NORM[token]
    if len(token) >= 4:
        candidates = [full for norm, full in _REGION_NORM.items() if norm.startswith(token)]
        if len(candidates) == 1:
            return candidates[0]
    return None


def parse_filename(file_name: str) -> tuple[str | None, dt.date | None]:
    """Best-effort (region, date) from the filename alone -- a second
    opinion only, per the spec; never trusted on its own."""
    stem = Path(file_name).stem
    s = re.sub(r"(?i)^\s*copy of\s+", "", stem)
    s = re.sub(r"\s*\(\d+\)\s*$", "", s)

    date_val = None
    m = _DATE_8.search(s)
    if m:
        dd, mm, yyyy = m.groups()
        year = int(yyyy)
    else:
        m = _DATE_6.search(s)
        if m:
            dd, mm, yy = m.groups()
            year = 2000 + int(yy)
    if m:
        try:
            date_val = dt.date(year, int(mm), int(dd))
        except ValueError:
            date_val = None
        s_no_date = s[:m.start()] + s[m.end():]
    else:
        s_no_date = s

    region_token = re.sub(r"[^A-Za-z]", "", s_no_date).upper()
    region_val = _match_region_token(region_token)
    return region_val, date_val


@dataclass
class ControlResolution:
    status: str  # "resolved" | "pause" | "reject"
    region: str | None = None
    date: dt.date | None = None
    region_source: str | None = None  # "sheet" | "filename"
    date_source: str | None = None    # "sheet" | "filename_confirmed"
    reject_code: str | None = None
    reject_message: str | None = None
    # Pause case: whichever of region/date is NOT in question is already
    # filled in above (region/region_source or date/date_source), so the
    # caller only ever has to ask about the side that's actually paused.
    pause_on: str | None = None       # "region" | "date" | "both"
    pause_code: str | None = None     # "R05" | "R13"
    pause_message: str | None = None
    sheet_region: str | None = None
    sheet_date: dt.date | None = None
    filename_region: str | None = None
    filename_date: dt.date | None = None
    warnings: list[dict] = field(default_factory=list)


def parse_control_sheet(wb, file_name: str) -> ControlResolution:
    filename_region, filename_date = parse_filename(file_name)
    warnings: list[dict] = []

    sheet_region_raw = wb["Control"]["L3"].value
    sheet_region_text = str(sheet_region_raw).strip() if sheet_region_raw not in (None, "") else ""
    resolved_sheet_region = _match_region_token(re.sub(r"[^A-Za-z]", "", sheet_region_text).upper()) if sheet_region_text else None

    if sheet_region_text and not resolved_sheet_region:
        return ControlResolution(status="reject", reject_code="R10",
                                 reject_message=f"Control!L3 = '{sheet_region_text}' is not one of the 10 known regions.")

    region_val, region_source, region_pause = None, None, False
    if resolved_sheet_region:
        if filename_region and filename_region != resolved_sheet_region:
            region_pause = True
        else:
            region_val, region_source = resolved_sheet_region, "sheet"
    else:
        if filename_region:
            region_val, region_source = filename_region, "filename"
            warnings.append(issue("R12", sheet="Control", cell="L3", value_found="(blank)",
                                  expected=filename_region, action=f"Region taken from filename ({filename_region})"))
        else:
            return ControlResolution(status="reject", reject_code="R10",
                                     reject_message="Control!L3 is blank and the filename region can't be read.")

    sheet_date_raw = wb["Control"]["L4"].value
    sheet_date = None
    if isinstance(sheet_date_raw, dt.datetime):
        sheet_date = sheet_date_raw.date()
    elif isinstance(sheet_date_raw, dt.date):
        sheet_date = sheet_date_raw

    today = dt.date.today()
    date_val, date_source, date_pause, date_pause_code = None, None, False, None
    if sheet_date:
        if sheet_date > today:
            return ControlResolution(status="reject", reject_code="R04",
                                     reject_message=f"Control!L4 ({sheet_date}) is a date in the future.")
        if filename_date and filename_date != sheet_date:
            date_pause, date_pause_code = True, "R05"
        else:
            date_val, date_source = sheet_date, "sheet"
    else:
        if filename_date:
            date_pause, date_pause_code = True, "R13"
        else:
            return ControlResolution(status="reject", reject_code="R04",
                                     reject_message="Control!L4 is blank and the filename date can't be read.")

    if region_pause or date_pause:
        pause_on = "both" if (region_pause and date_pause) else ("region" if region_pause else "date")
        code = "R05" if (region_pause or date_pause_code == "R05") else "R13"
        parts = []
        if region_pause:
            parts.append(f"Sheet says region {resolved_sheet_region} (Control!L3), filename says {filename_region}.")
        if date_pause:
            if date_pause_code == "R05":
                parts.append(f"Sheet says date {sheet_date} (Control!L4), filename says {filename_date}.")
            else:
                parts.append(f"Control!L4 is blank; the filename says {filename_date}.")
        return ControlResolution(
            status="pause", pause_on=pause_on, pause_code=code, pause_message=" ".join(parts),
            # whichever side is NOT paused is already resolved -- carried
            # through so the caller never has to re-derive it.
            region=region_val, region_source=region_source,
            date=date_val, date_source=date_source,
            sheet_region=resolved_sheet_region, sheet_date=sheet_date,
            filename_region=filename_region, filename_date=filename_date,
        )

    return ControlResolution(status="resolved", region=region_val, date=date_val,
                             region_source=region_source, date_source=date_source, warnings=warnings)


# -----------------------------------------------------------------
# Row/cell parsing (section 3b) -- once region+date are resolved.
# -----------------------------------------------------------------

def _coord(row: int, col: int) -> str:
    return f"{openpyxl.utils.get_column_letter(col)}{row}"


def _check_layout(ws) -> dict | None:
    """R06: row 2 hour labels at M,Q,U...DA and row 3 headers A-P. Returns
    an issue dict for the FIRST mismatch found, or None if the layout is fine."""
    for h in range(1, 25):
        col = 13 + 4 * (h - 1)
        expected = f"{h:02d}:00"
        found = ws.cell(2, col).value
        if str(found or "").strip() != expected:
            return issue("R06", sheet="Record and Accounting", cell=_coord(2, col),
                        value_found=str(found), expected=expected,
                        action=f"Row 2 column {openpyxl.utils.get_column_letter(col)}: expected '{expected}', "
                               f"found '{found}' (a column was likely inserted or deleted)")
    for i, hdr in enumerate(FIXED_HEADERS, start=1):
        found = ws.cell(3, i).value
        if str(found or "").strip().upper() != hdr.upper():
            return issue("R06", sheet="Record and Accounting", cell=_coord(3, i),
                        value_found=str(found), expected=hdr,
                        action=f"Row 3 column {openpyxl.utils.get_column_letter(i)}: expected '{hdr}', found '{found}'")
    for h in range(1, 25):
        base = 13 + 4 * (h - 1)
        for j, hdr in enumerate(BLOCK_HEADERS):
            found = ws.cell(3, base + j).value
            if str(found or "").strip().upper() != hdr.upper():
                return issue("R06", sheet="Record and Accounting", cell=_coord(3, base + j),
                            value_found=str(found), expected=hdr,
                            action=f"Row 3 column {openpyxl.utils.get_column_letter(base + j)}: expected '{hdr}', found '{found}'"
                                   " (a column was likely inserted or deleted)")
    return None


def _find_total_row(ws) -> int | None:
    for r in range(4, ws.max_row + 1):
        if str(ws.cell(r, 1).value or "").strip().upper() == "TOTAL":
            return r
    return None


def _clean_number(v, row: int, col: int, field_name: str, issues: list[dict]) -> float | None:
    """R30 (text in numeric cell): returns None and records an issue if v
    isn't a usable number."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    issues.append(issue("R30", sheet="Record and Accounting", cell=_coord(row, col), value_found=str(v),
                        expected="a number", action=f"{field_name}: text '{v}' found, stored as blank"))
    return None


@dataclass
class AliasMaps:
    daily_workbook: dict[tuple[str, str], int]
    any_source: dict[tuple[str, str], int]


def load_alias_maps(engine) -> AliasMaps:
    """One query for the whole feeder_alias table (976 rows, cheap), used
    to resolve every row of a workbook in memory instead of 1-2 DB round
    trips per row -- the same per-row-query mistake that made phase 1's
    seed script take 10+ minutes before it was batched; resolving a
    59-feeder workbook one row at a time measured at ~117 seconds before
    this fix, likely worse for a bigger region or several files at once."""
    with engine.connect() as con:
        rows = con.execute(text("SELECT source, station_norm, name_norm, feeder_id FROM feeder_alias")).fetchall()
    daily_workbook, any_source = {}, {}
    for source, sn, fn, feeder_id in rows:
        any_source.setdefault((sn, fn), feeder_id)
        if source == "daily_workbook":
            daily_workbook[(sn, fn)] = feeder_id
    return AliasMaps(daily_workbook=daily_workbook, any_source=any_source)


def resolve_feeder_cached(maps: AliasMaps, station: str, feeder: str) -> int | None:
    """Exact (source='daily_workbook') match first, then any-source
    normalized match at the same station -- same priority as
    resolve_feeder(), looked up in the pre-loaded maps instead of the DB."""
    key = (normalize_station(station), normalize_feeder(feeder))
    return maps.daily_workbook.get(key) or maps.any_source.get(key)


def resolve_feeder(engine, station: str, feeder: str) -> int | None:
    """Single-lookup convenience (e.g. resolving one outages row at query
    time, phase 3+) -- loads the maps fresh each call, so NOT for use in
    a loop; see load_alias_maps()/resolve_feeder_cached() for that."""
    return resolve_feeder_cached(load_alias_maps(engine), station, feeder)


@dataclass
class ParseResult:
    region: str
    date: dt.date
    status: str = "ok"  # "ok" | "reject" | "pause"
    reject_code: str | None = None
    reject_message: str | None = None
    pause_code: str | None = None       # "R14"
    pause_message: str | None = None
    daily_rows: list[dict] = field(default_factory=list)       # -> feeder_daily
    forecast_rows: list[dict] = field(default_factory=list)    # -> feeder_hourly_forecast
    reading_rows: list[dict] = field(default_factory=list)     # -> feeder_meter_reading
    unmatched_rows: list[dict] = field(default_factory=list)   # -> feeder_upload_staging (row_data)
    issues: list[dict] = field(default_factory=list)
    feeders_read: int = 0
    rows_saved: int = 0
    rows_skipped: int = 0


_STALE_TOLERANCE_MWH = 0.5


def _check_stale_or_duplicate(engine, region: str, resolved_date: dt.date,
                               daily_rows: list[dict], prev_closing: dict[int, float]) -> dict | None:
    """R14 (file-level pause): most opening readings don't follow on from
    the previous day's closings, OR this file's openings are identical to
    another already-saved date's -- either way Control!L4 may be wrong.
    One query covers every other stored date at once (not one query per
    date), so this stays cheap as the table grows over months."""
    comparable = [(d["feeder_id"], d["opening_meter_mwh"]) for d in daily_rows if d["opening_meter_mwh"] is not None]

    stale_total = stale_off = 0
    for feeder_id, opening in comparable:
        prev = prev_closing.get(feeder_id)
        if prev is not None:
            stale_total += 1
            if abs(opening - prev) > _STALE_TOLERANCE_MWH:
                stale_off += 1
    if stale_total >= 10 and stale_off > stale_total / 2:
        return dict(code="R14", message=f"{stale_off} of {stale_total} feeders' opening readings don't follow on "
                                        f"from {resolved_date - dt.timedelta(days=1)}'s closing readings "
                                        f"(off by more than {_STALE_TOLERANCE_MWH} MWh). Control!L4 may be wrong.")

    with engine.connect() as con:
        other_rows = con.execute(text(
            "SELECT reading_date, feeder_id, opening_meter_mwh FROM feeder_daily "
            "WHERE region=:r AND reading_date != :d AND opening_meter_mwh IS NOT NULL"
        ), dict(r=region, d=resolved_date)).fetchall()
    by_date: dict[dt.date, dict[int, float]] = {}
    for rd, feeder_id, opening in other_rows:
        by_date.setdefault(rd, {})[feeder_id] = float(opening)

    for other_date, openings in by_date.items():
        match_total = match_same = 0
        for feeder_id, opening in comparable:
            other = openings.get(feeder_id)
            if other is not None:
                match_total += 1
                if abs(opening - other) < 0.01:
                    match_same += 1
        if match_total >= 10 and match_same > match_total / 2:
            return dict(code="R14", message=f"This file's opening readings are identical to {other_date}'s "
                                            f"saved file for {match_same} of {match_total} feeders -- "
                                            f"the date in Control!L4 may be wrong.")
    return None


def parse_workbook(path: str | Path, region: str, resolved_date: dt.date, engine) -> ParseResult:
    wb = openpyxl.load_workbook(path, data_only=True)
    if "Control" not in wb.sheetnames or "Record and Accounting" not in wb.sheetnames:
        return ParseResult(region=region, date=resolved_date, status="reject", reject_code="R03",
                           reject_message='Sheet "Control" or "Record and Accounting" is missing.')
    ws = wb["Record and Accounting"]

    layout_issue = _check_layout(ws)
    if layout_issue:
        return ParseResult(region=region, date=resolved_date, status="reject", reject_code="R06",
                           reject_message=layout_issue["action"])

    alias_maps = load_alias_maps(engine)

    with engine.connect() as con:
        prev_closing = {feeder_id: float(closing) for feeder_id, closing in con.execute(text(
            "SELECT feeder_id, closing_meter_mwh FROM feeder_daily WHERE region=:r AND reading_date=:d "
            "AND closing_meter_mwh IS NOT NULL"
        ), dict(r=region, d=resolved_date - dt.timedelta(days=1))).fetchall()}

    total_row = _find_total_row(ws)
    if total_row is None:
        return ParseResult(region=region, date=resolved_date, status="reject", reject_code="R07",
                           reject_message='No row with "TOTAL" in column A was found.')

    result = ParseResult(region=region, date=resolved_date)
    next_day = resolved_date + dt.timedelta(days=1)
    seen_keys: set[tuple[str, str]] = set()
    blank_region_cells: list[str] = []

    for r in range(4, total_row):
        station = ws.cell(r, 4).value
        feeder = ws.cell(r, 8).value
        row_has_numbers = any(isinstance(ws.cell(r, c).value, (int, float)) for c in range(9, 109))
        if (station is None or not str(station).strip()) and (feeder is None or not str(feeder).strip()) and not row_has_numbers:
            continue  # fully blank row, ignored silently

        row_region_raw = ws.cell(r, 1).value
        row_region = str(row_region_raw).strip() if row_region_raw else ""
        if not row_region:
            blank_region_cells.append(_coord(r, 1))
        elif normalize_station(row_region) != normalize_station(region) and \
             re.sub(r"[^A-Z]", "", row_region.upper()) != re.sub(r"[^A-Z]", "", region.upper()):
            return ParseResult(region=region, date=resolved_date, status="reject", reject_code="R11",
                               reject_message=f"{_coord(r, 1)}: found '{row_region}', expected '{region}'")

        if station is None or not str(station).strip() or feeder is None or not str(feeder).strip():
            result.issues.append(issue("R20", sheet="Record and Accounting", cell=_coord(r, 4),
                                       station_raw=str(station or ""), feeder_raw=str(feeder or ""),
                                       action="Row skipped: missing feeder or station name"))
            result.rows_skipped += 1
            continue

        station_s, feeder_s = str(station).strip(), str(feeder).strip()
        dup_key = (normalize_station(station_s), normalize_feeder(feeder_s))
        if dup_key in seen_keys:
            result.issues.append(issue("R21", sheet="Record and Accounting", cell=_coord(r, 4),
                                       station_raw=station_s, feeder_raw=feeder_s,
                                       action="Row skipped: duplicate of an earlier row"))
            result.rows_skipped += 1
            continue
        seen_keys.add(dup_key)

        disco = str(ws.cell(r, 2).value or "").strip() or None
        area_control = str(ws.cell(r, 3).value or "").strip() or None
        transformer = str(ws.cell(r, 5).value or "").strip() or None
        rating_raw = ws.cell(r, 6).value
        rating = str(rating_raw).strip() if rating_raw is not None else None
        band_raw = ws.cell(r, 7).value
        band = str(band_raw).strip() or None if band_raw is not None else None
        if not band:
            result.issues.append(issue("R34", sheet="Record and Accounting", cell=_coord(r, 7),
                                       station_raw=station_s, feeder_raw=feeder_s, value_found="(blank)",
                                       action="Band blank: pricing falls back to the SLA band or Band A"))
        tcn_limit = _clean_number(ws.cell(r, 9).value, r, 9, "TCN Limits", result.issues)
        base_load = _clean_number(ws.cell(r, 10).value, r, 10, "Disco Base Load", result.issues)
        peak_load = _clean_number(ws.cell(r, 11).value, r, 11, "Disco Peak Load", result.issues)
        opening = _clean_number(ws.cell(r, 12).value, r, 12, "Opening meter reading", result.issues)

        feeder_id = resolve_feeder_cached(alias_maps, station_s, feeder_s)
        if feeder_id is None:
            row_data = dict(region=region, disco=disco, area_control=area_control, station=station_s,
                            transformer=transformer, rating=rating, feeder_band=band, feeder_name=feeder_s,
                            tcn_limit_mw=tcn_limit, disco_base_load_mw=base_load, disco_peak_load_mw=peak_load,
                            opening_meter_mwh=opening, reading_date=str(resolved_date),
                            hours=[dict(h=h, forecast=_clean_number(ws.cell(r, 13 + 4*(h-1)).value, r, 13+4*(h-1), f"hour {h} forecast", []),
                                       reading=_clean_number(ws.cell(r, 14 + 4*(h-1)).value, r, 14+4*(h-1), f"hour {h} reading", []))
                                   for h in range(1, 25)])
            result.unmatched_rows.append(row_data)
            result.issues.append(issue("R22", sheet="Record and Accounting", cell=_coord(r, 8),
                                       station_raw=station_s, feeder_raw=feeder_s,
                                       action="Feeder not matched to the registry; held for review"))
            result.rows_skipped += 1
            continue

        result.feeders_read += 1
        result.daily_rows.append(dict(
            feeder_id=feeder_id, reading_date=resolved_date, region=region, disco_seen=disco,
            feeder_band_seen=band, tcn_limit_mw=tcn_limit, disco_base_load_mw=base_load,
            disco_peak_load_mw=peak_load, opening_meter_mwh=opening, source_file=Path(path).name,
            station_raw=station_s, feeder_raw=feeder_s,
        ))

        prev_close_for_feeder = prev_closing.get(feeder_id)
        if opening is not None and prev_close_for_feeder is not None and \
           abs(opening - prev_close_for_feeder) > _STALE_TOLERANCE_MWH:
            result.issues.append(issue("R35", sheet="Record and Accounting", cell=_coord(r, 12),
                                       station_raw=station_s, feeder_raw=feeder_s, value_found=str(opening),
                                       expected=f"~{prev_close_for_feeder} (previous day's 24:00 reading)",
                                       action="Opening reading differs from the previous day's 24:00 reading"))

        prev_reading = opening
        result.reading_rows.append(dict(
            feeder_id=feeder_id, reading_date=resolved_date, reading_time=dt.time(0, 0),
            reading_at=dt.datetime.combine(resolved_date, dt.time(0, 0)), meter_reading_mwh=opening,
            actual_mwh=None, source_date=resolved_date, source_label=0, flag=None,
        ))

        for h in range(1, 25):
            col_f = 13 + 4 * (h - 1)
            col_m = col_f + 1
            forecast = _clean_number(ws.cell(r, col_f).value, r, col_f, f"hour {h} forecast", result.issues)
            if forecast is not None and forecast < 0:
                result.issues.append(issue("R31", sheet="Record and Accounting", cell=_coord(r, col_f),
                                           station_raw=station_s, feeder_raw=feeder_s, value_found=str(forecast),
                                           action=f"Hour {h} forecast was negative, stored as blank"))
                forecast = None
            reading = _clean_number(ws.cell(r, col_m).value, r, col_m, f"hour {h} reading", result.issues)

            if h == 24:
                slot_date, slot_time = next_day, dt.time(0, 0)
                source_label = 24
            else:
                slot_date, slot_time = resolved_date, dt.time(h, 0)
                source_label = h
            slot_start = dt.datetime.combine(slot_date, slot_time)

            result.forecast_rows.append(dict(
                feeder_id=feeder_id, slot_date=slot_date, slot_time=slot_time, slot_start=slot_start,
                forecast_mw=forecast, source_date=resolved_date, source_label=source_label,
            ))

            flag = None
            actual_mwh = None
            if reading is not None:
                if prev_reading is not None:
                    if reading < prev_reading:
                        flag = "R32"
                        result.issues.append(issue("R32", sheet="Record and Accounting", cell=_coord(r, col_m),
                                                   station_raw=station_s, feeder_raw=feeder_s,
                                                   value_found=str(reading), expected=f">= {prev_reading}",
                                                   action=f"Hour {h} reading lower than previous reading; energy left blank"))
                    else:
                        actual_mwh = reading - prev_reading
                        limit_ok = True
                        if tcn_limit is not None and actual_mwh > tcn_limit * 1.5:
                            limit_ok = False
                        if limit_ok:
                            try:
                                rating_f = float(rating) if rating is not None else None
                            except ValueError:
                                rating_f = None
                            if rating_f is not None and actual_mwh > 2 * rating_f:
                                limit_ok = False
                        if not limit_ok:
                            flag = "R33"
                            result.issues.append(issue("R33", sheet="Record and Accounting", cell=_coord(r, col_m),
                                                       station_raw=station_s, feeder_raw=feeder_s,
                                                       value_found=str(actual_mwh),
                                                       action=f"Hour {h} energy above the TCN limit/rating; stored and flagged"))
                prev_reading = reading

            result.reading_rows.append(dict(
                feeder_id=feeder_id, reading_date=slot_date, reading_time=slot_time, reading_at=slot_start,
                meter_reading_mwh=reading, actual_mwh=actual_mwh, source_date=resolved_date,
                source_label=source_label, flag=flag,
            ))

        result.rows_saved += 1

    if blank_region_cells:
        result.issues.append(issue("R15", sheet="Record and Accounting", cell=", ".join(blank_region_cells),
                                   action=f"{len(blank_region_cells)} blank region cell(s) filled with '{region}'"))

    if result.feeders_read == 0 and not result.unmatched_rows:
        return ParseResult(region=region, date=resolved_date, status="reject", reject_code="R08",
                           reject_message="Fewer than 1 feeder row was found.")

    stale = _check_stale_or_duplicate(engine, region, resolved_date, result.daily_rows, prev_closing)
    if stale:
        result.status = "pause"
        result.pause_code = stale["code"]
        result.pause_message = stale["message"]

    return result
