"""Postgres storage for the Incident & Outage Request app.

Deliberately separate from both other apps' db.py -- this app has its own
database (DATABASE_INCIDENT_URL) so all three apps' data stay fully
independent, matching the pattern already established between the 33kV
app and the TCN 330/132kV app.

Covers Outage Request and Incident Report (Daily Max/Min follows later as
the same pattern repeated: its own table, same sync_runs audit trail).
Every pull is additive and permanent -- rows are inserted or updated by
sync_id, never removed or hidden by a sync.
"""
import os
import re
from io import StringIO

import bcrypt
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

_DATE_COLS = ("application_date", "scheduled_start_date", "scheduled_end_date")


def _parse_date(value):
    """Parse a date value with dayfirst=True -- sheet dates arrive as
    'DD/MM/YYYY' strings, and passing that straight to Postgres lets the
    server's own (MDY) date parsing silently swap day/month. Same fix
    already applied in TCN-Outage-Manager-main/db.py."""
    if value is None or value == "":
        return None
    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _parse_time(value):
    """Parse an 'HH:MM' (or 'HH:MM:SS') string to a time object; anything
    unparseable (blank, sentinel text) becomes NULL rather than erroring."""
    if value is None or value == "":
        return None
    parsed = pd.to_datetime(str(value).strip(), errors="coerce")
    return None if pd.isna(parsed) else parsed.time()


_DURATION_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>hour|hr|hours|hrs|min|mins|minute|minutes)",
    re.IGNORECASE,
)


def _parse_duration_to_minutes(raw):
    """Best-effort numeric parse of free-text durations like '20MINS',
    '1HOUR', '3 HOURS', '5 HOURS ' -- returns None if nothing matches
    rather than guessing. The raw text is always kept as-is alongside this
    in estimated_duration_raw, so nothing is lost if the parse misses."""
    if raw is None:
        return None
    text_val = str(raw).strip()
    if not text_val:
        return None
    total_minutes = 0.0
    matched = False
    for m in _DURATION_RE.finditer(text_val):
        matched = True
        value = float(m.group("value"))
        unit = m.group("unit").lower()
        total_minutes += value * 60 if unit.startswith(("hour", "hr")) else value
    return total_minutes if matched else None


# Sentinel tokens found in the real sheets are operationally distinct --
# NOT interchangeable placeholders for "missing data". A NUMERIC column
# can't store any of them, so the parser coerces the number to NULL *and*
# separately records which one it saw, so the meaning survives instead of
# collapsing into an indistinguishable NULL.
_SENTINEL_LABELS = {
    "NIL": "NIL",
    "NILL": "NIL",
    "N/A": "NOT_AVAILABLE",
    "NA": "NOT_AVAILABLE",
    "O/S": "OUT_OF_SERVICE",
    "OS": "OUT_OF_SERVICE",
    "I/S": "IN_SERVICE",
    "IS": "IN_SERVICE",
    "T/F": "TRANSFORMER_FAULT",
    "TF": "TRANSFORMER_FAULT",
}


def _parse_numeric_with_status(value):
    """Returns (numeric_or_none, sentinel_label_or_none).

    A blank cell yields (None, None) -- no sentinel was seen, it was just
    empty. A recognized sentinel token yields (None, LABEL). Anything else
    is parsed as a plain number.
    """
    if value is None:
        return None, None
    text_val = str(value).strip().upper()
    if not text_val:
        return None, None
    if text_val in _SENTINEL_LABELS:
        return None, _SENTINEL_LABELS[text_val]
    numeric = pd.to_numeric(value, errors="coerce")
    return (None if pd.isna(numeric) else float(numeric)), None


@st.cache_resource
def get_engine():
    url = os.getenv("DATABASE_INCIDENT_URL")
    if not url:
        raise RuntimeError("DATABASE_INCIDENT_URL is not set")
    return create_engine(url, pool_pre_ping=True)


# -----------------------------
# PASSWORDS / USERS
# -----------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def authenticate(username: str, password: str) -> bool:
    if not username or not password:
        return False
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT password_hash FROM users WHERE username = :u"), {"u": username}
        ).fetchone()
    if row is None:
        return False
    return _verify_password(password, row[0])


def get_user(username: str) -> dict | None:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT username, name, role, region FROM users WHERE username = :u"),
            {"u": username},
        ).mappings().fetchone()
    return dict(row) if row else None


def get_user_role_and_region(username: str) -> tuple:
    """Return (role, region) for the given username in a single query.

    region is None for an admin (and if the user isn't found).
    """
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT role, region FROM users WHERE username = :u"), {"u": username}
        ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def list_users() -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql_query(
        text("SELECT username, name, role, region, created_at FROM users ORDER BY created_at"),
        engine,
    )


def create_user(username: str, password_hash: str, name: str, role: str, region: str | None) -> None:
    engine = get_engine()
    query = text("""
        INSERT INTO users (username, password_hash, name, role, region)
        VALUES (:u, :p, :n, :r, :region)
    """)
    with engine.begin() as conn:
        conn.execute(query, {"u": username, "p": password_hash, "n": name, "r": role, "region": region})


def update_user(username: str, *, password_hash: str | None = None, name: str | None = None,
                 role: str | None = None, region_explicit: bool = False, region: str | None = None) -> None:
    engine = get_engine()
    sets, params = [], {"u": username}
    if password_hash is not None:
        sets.append("password_hash = :p")
        params["p"] = password_hash
    if name is not None:
        sets.append("name = :n")
        params["n"] = name
    if role is not None:
        sets.append("role = :r")
        params["r"] = role
    if region_explicit:
        sets.append("region = :region")
        params["region"] = region
    if not sets:
        return
    query = text(f"UPDATE users SET {', '.join(sets)} WHERE username = :u")
    with engine.begin() as conn:
        conn.execute(query, params)


def delete_user(username: str) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE username = :u"), {"u": username})


# -----------------------------
# WEB APP SETTINGS (per-region Apps Script Web App URLs)
# -----------------------------

def get_web_app_url(region: str) -> str | None:
    if not region:
        return None
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT web_app_url FROM web_app_settings WHERE region = :r"), {"r": region.upper()}
        ).fetchone()
    return (row[0] or None) if row else None


def list_web_app_settings() -> pd.DataFrame:
    engine = get_engine()
    return pd.read_sql_query(
        text("SELECT region, web_app_url, updated_at, updated_by FROM web_app_settings ORDER BY region"),
        engine,
    )


def upsert_web_app_url(region: str, web_app_url, updated_by: str | None) -> None:
    engine = get_engine()
    url = "" if web_app_url is None else str(web_app_url).strip()
    query = text("""
        INSERT INTO web_app_settings (region, web_app_url, updated_at, updated_by)
        VALUES (:r, :u, now(), :by)
        ON CONFLICT (region) DO UPDATE
        SET web_app_url = EXCLUDED.web_app_url, updated_at = now(), updated_by = EXCLUDED.updated_by
    """)
    with engine.begin() as conn:
        conn.execute(query, {"r": region.upper(), "u": url or None, "by": updated_by})


# -----------------------------
# SYNC RUNS (audit trail)
# -----------------------------

def start_sync_run(region: str, report_type: str, sheet_id: str, tab_name: str, rows_received: int) -> int:
    engine = get_engine()
    query = text("""
        INSERT INTO sync_runs (region, report_type, source_sheet_id, source_tab_name, rows_received, status)
        VALUES (:region, :report_type, :sheet_id, :tab_name, :rows_received, 'running')
        RETURNING id
    """)
    with engine.begin() as conn:
        run_id = conn.execute(query, {
            "region": region, "report_type": report_type, "sheet_id": sheet_id,
            "tab_name": tab_name, "rows_received": rows_received,
        }).scalar_one()
    return run_id


def finish_sync_run(run_id: int, inserted: int, updated: int, rejected: int,
                     status: str, error_message: str | None = None) -> None:
    engine = get_engine()
    query = text("""
        UPDATE sync_runs
        SET completed_at = now(), rows_inserted = :inserted, rows_updated = :updated,
            rows_rejected = :rejected, status = :status, error_message = :error_message
        WHERE id = :run_id
    """)
    with engine.begin() as conn:
        conn.execute(query, {
            "run_id": run_id, "inserted": inserted, "updated": updated,
            "rejected": rejected, "status": status, "error_message": error_message,
        })


def read_sync_runs(region: str | None = None, limit: int = 50) -> pd.DataFrame:
    engine = get_engine()
    where = "WHERE region = :region" if region else ""
    query = text(f"""
        SELECT id, region, report_type, source_sheet_id, source_tab_name, started_at,
               completed_at, rows_received, rows_inserted, rows_updated, rows_rejected,
               status, error_message
        FROM sync_runs
        {where}
        ORDER BY started_at DESC
        LIMIT :limit
    """)
    params = {"limit": limit}
    if region:
        params["region"] = region
    return pd.read_sql_query(query, engine, params=params)


def latest_sync_run(region: str, report_type: str) -> dict | None:
    engine = get_engine()
    query = text("""
        SELECT id, started_at, completed_at, rows_received, rows_inserted, rows_updated,
               rows_rejected, status, error_message
        FROM sync_runs
        WHERE region = :region AND report_type = :report_type
        ORDER BY started_at DESC
        LIMIT 1
    """)
    with engine.connect() as conn:
        row = conn.execute(query, {"region": region, "report_type": report_type}).mappings().fetchone()
    return dict(row) if row else None


# -----------------------------
# OUTAGE REQUESTS
# -----------------------------

_OUTAGE_REQUEST_COLUMNS = [
    "sync_id", "region", "application_date", "station", "voltage_level", "equipment_name",
    "description_of_work", "scheduled_start_date", "scheduled_start_time", "scheduled_end_date",
    "scheduled_end_time", "estimated_duration_raw", "estimated_duration_minutes",
    "expected_load_mw", "expected_load_status", "disco_affected", "personnel_informed",
    "applicant_name", "remarks", "sheet_row_number", "source_sheet_id", "source_tab_name",
]

# maps db column -> the exact header text expected in the sheet's own header row
_DISPLAY_ALIASES = {
    "region": "Region",
    "application_date": "Application Date",
    "station": "Station",
    "voltage_level": "Voltage Level",
    "equipment_name": "Equipment Name/Nomenclature",
    "description_of_work": "Description of Work",
    "scheduled_start_date": "Scheduled Start Date",
    "scheduled_start_time": "Scheduled Start Time",
    "scheduled_end_date": "Scheduled End Date",
    "scheduled_end_time": "Scheduled End Time",
    "estimated_duration_raw": "Estimated Duration (hrs)",
    "expected_load_mw": "Expected Load Affected (MW)",
    "disco_affected": "DISCO Affected",
    "personnel_informed": "Name of personnel informed",
    "applicant_name": "Name/Designation of Applicant",
    "remarks": "Remarks/Notes",
    "sheet_row_number": "S/N",
}


def _normalize_row(raw: dict, sheet_id: str, tab_name: str) -> dict | None:
    """Map one raw JSON row (as sent by Apps Script, keyed by sheet header
    text) into the outage_requests column shape. Returns None if the row
    is missing its sync_id (rejected, not silently dropped -- caller is
    expected to report this back)."""
    sync_id = raw.get("sync_id") or raw.get("Sync ID")
    if not sync_id:
        return None

    load_value, load_status = _parse_numeric_with_status(raw.get(_DISPLAY_ALIASES["expected_load_mw"]))
    duration_raw = raw.get(_DISPLAY_ALIASES["estimated_duration_raw"])

    row = {
        "sync_id": str(sync_id).strip(),
        "region": raw.get(_DISPLAY_ALIASES["region"]) or raw.get("region"),
        "application_date": _parse_date(raw.get(_DISPLAY_ALIASES["application_date"])),
        "station": raw.get(_DISPLAY_ALIASES["station"]),
        "voltage_level": raw.get(_DISPLAY_ALIASES["voltage_level"]),
        "equipment_name": raw.get(_DISPLAY_ALIASES["equipment_name"]),
        "description_of_work": raw.get(_DISPLAY_ALIASES["description_of_work"]),
        "scheduled_start_date": _parse_date(raw.get(_DISPLAY_ALIASES["scheduled_start_date"])),
        "scheduled_start_time": _parse_time(raw.get(_DISPLAY_ALIASES["scheduled_start_time"])),
        "scheduled_end_date": _parse_date(raw.get(_DISPLAY_ALIASES["scheduled_end_date"])),
        "scheduled_end_time": _parse_time(raw.get(_DISPLAY_ALIASES["scheduled_end_time"])),
        "estimated_duration_raw": duration_raw,
        "estimated_duration_minutes": _parse_duration_to_minutes(duration_raw),
        "expected_load_mw": load_value,
        "expected_load_status": load_status,
        "disco_affected": raw.get(_DISPLAY_ALIASES["disco_affected"]),
        "personnel_informed": raw.get(_DISPLAY_ALIASES["personnel_informed"]),
        "applicant_name": raw.get(_DISPLAY_ALIASES["applicant_name"]),
        "remarks": raw.get(_DISPLAY_ALIASES["remarks"]),
        "sheet_row_number": pd.to_numeric(raw.get(_DISPLAY_ALIASES["sheet_row_number"]), errors="coerce"),
        "source_sheet_id": sheet_id,
        "source_tab_name": tab_name,
    }
    if pd.isna(row["sheet_row_number"]):
        row["sheet_row_number"] = None
    else:
        row["sheet_row_number"] = int(row["sheet_row_number"])
    return row


def upsert_outage_requests(rows: list, run_id: int, region: str, sheet_id: str, tab_name: str) -> dict:
    """Bulk upsert keyed on sync_id (ON CONFLICT DO UPDATE), tagging every
    touched row with last_sync_run_id.

    Pulls are ADDITIVE and permanent -- new rows are inserted, matching
    rows (same sync_id) are updated in place, and nothing is ever removed
    or hidden by a pull. A row that disappears from the sheet just stays
    in the database as it was. (The is_active column is kept for possible
    manual use, but sync never flips it to false.)"""
    normalized, rejected = [], []
    for i, raw in enumerate(rows):
        row = _normalize_row(raw, sheet_id, tab_name)
        if row is None:
            rejected.append({"row": i, "reason": "missing sync_id"})
        elif not row["application_date"] and not row["scheduled_start_date"]:
            rejected.append({"row": i, "reason": "missing both application_date and scheduled_start_date"})
        else:
            normalized.append(row)

    if not normalized:
        return {"inserted": 0, "updated": 0, "rejected": rejected}

    df = pd.DataFrame(normalized)[_OUTAGE_REQUEST_COLUMNS]

    engine = get_engine()
    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cur.execute("DROP TABLE IF EXISTS temp_outage_requests")
        cur.execute("""
            CREATE TEMP TABLE temp_outage_requests (
                sync_id TEXT, region TEXT, application_date DATE, station TEXT,
                voltage_level TEXT, equipment_name TEXT, description_of_work TEXT,
                scheduled_start_date DATE, scheduled_start_time TIME, scheduled_end_date DATE,
                scheduled_end_time TIME, estimated_duration_raw TEXT,
                estimated_duration_minutes NUMERIC, expected_load_mw NUMERIC,
                expected_load_status TEXT, disco_affected TEXT, personnel_informed TEXT,
                applicant_name TEXT, remarks TEXT, sheet_row_number INT,
                source_sheet_id TEXT, source_tab_name TEXT
            )
        """)
        # de-dupe on sync_id within this batch (last one wins) -- Postgres
        # can't ON CONFLICT the same target row twice in one statement
        df = df.drop_duplicates(subset=["sync_id"], keep="last")
        buffer = StringIO(df.to_csv(index=False))
        next(buffer)  # skip header
        cur.copy_expert("COPY temp_outage_requests FROM STDIN WITH CSV", buffer)

        update_cols = [c for c in _OUTAGE_REQUEST_COLUMNS if c != "sync_id"]
        cur.execute(f"""
            INSERT INTO outage_requests ({", ".join(_OUTAGE_REQUEST_COLUMNS)}, is_active, last_sync_run_id)
            SELECT {", ".join(_OUTAGE_REQUEST_COLUMNS)}, true, %s FROM temp_outage_requests
            WHERE sync_id IS NOT NULL
            ON CONFLICT (sync_id) DO UPDATE SET
            {", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)},
            is_active = true, last_sync_run_id = %s, updated_at = now()
            RETURNING (xmax = 0) AS inserted
        """, (run_id, run_id))
        results = cur.fetchall()
        inserted = sum(1 for r in results if r[0])
        updated = len(results) - inserted

        # NO mark-and-sweep: pulls are additive and permanent. Rows that
        # vanish from the sheet are deliberately left untouched in the DB.
        cur.execute("DROP TABLE temp_outage_requests")
        raw_conn.commit()
        return {"inserted": inserted, "updated": updated, "rejected": rejected}
    finally:
        raw_conn.close()


def read_outage_requests(region: str | None = None, month: str | None = None) -> pd.DataFrame:
    """Every outage request on record, optionally region- and month-scoped.
    Deliberately does NOT filter on is_active -- once a row is pulled it
    stays visible permanently; nothing hides it.
    `month` is 'YYYY-MM'; matches on whichever date is populated
    (application_date, falling back to scheduled_start_date)."""
    engine = get_engine()
    clauses = []
    params: dict = {}
    if region:
        clauses.append("region = :region")
        params["region"] = region
    if month:
        clauses.append(
            "to_char(COALESCE(application_date, scheduled_start_date), 'YYYY-MM') = :month"
        )
        params["month"] = month
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = text(f"""
        SELECT * FROM outage_requests
        {where}
        ORDER BY COALESCE(scheduled_start_date, application_date) DESC,
                 scheduled_start_time DESC NULLS LAST
    """)
    return pd.read_sql_query(query, engine, params=params)


def list_available_months(region: str | None = None) -> list:
    engine = get_engine()
    where = "WHERE region = :region" if region else ""
    query = text(f"""
        SELECT DISTINCT to_char(COALESCE(application_date, scheduled_start_date), 'YYYY-MM') AS ym
        FROM outage_requests
        {where}
        ORDER BY ym DESC
    """)
    params = {"region": region} if region else {}
    with engine.connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [r[0] for r in rows if r[0]]


# -----------------------------
# INCIDENT REPORTS
# -----------------------------

_INCIDENT_REPORT_COLUMNS = [
    "sync_id", "region", "date_of_incident", "time_out", "location_station", "incident_type",
    "description", "immediate_action_taken", "affected_stations", "equipment_involved",
    "load_interrupted_mw", "load_interrupted_status", "potential_impact", "root_cause",
    "corrective_action", "date_of_closure", "time_in", "status", "remarks",
    "sheet_row_number", "source_sheet_id", "source_tab_name",
]

# maps db column -> the exact header text expected in the sheet's own
# header row. The Incident Report sheet has no "Region" column of its own
# (the whole sheet is already one region) -- Apps Script injects a
# lowercase "region" key on every row instead, handled below.
_INCIDENT_DISPLAY_ALIASES = {
    "date_of_incident": "DATE OF INCIDENT (DD/MM/YYYY)",
    "time_out": "TIME OUT",
    "location_station": "LOCATION / STATION",
    "incident_type": "TYPE OF INCIDENT (FORCED, PLANNED, EMERGENCY)",
    "description": "DESCRIPTION OF INCIDENT",
    "immediate_action_taken": "IMMEDIATE ACTION TAKEN",
    "affected_stations": "AFFECTED STATIONS",
    "equipment_involved": "EQUIPMENT INVOLVED",
    "load_interrupted_mw": "LOAD INTERRUPTED (MW)",
    "potential_impact": "POTENTIAL IMPACT",
    "root_cause": "ROOT CAUSE (PRELIMINARY)",
    "corrective_action": "CORRECTIVE ACTION (PROPOSED)",
    "date_of_closure": "DATE OF CLOSURE (DD/MM/YYYY)",
    "time_in": "TIME IN",
    "status": "STATUS",
    "remarks": "REMARKS",
    "sheet_row_number": "S/NO",
}


def _normalize_incident_row(raw: dict, sheet_id: str, tab_name: str) -> dict | None:
    """Map one raw JSON row (keyed by sheet header text) into the
    incident_reports column shape. Returns None if missing sync_id."""
    sync_id = raw.get("sync_id") or raw.get("Sync ID")
    if not sync_id:
        return None

    load_value, load_status = _parse_numeric_with_status(raw.get(_INCIDENT_DISPLAY_ALIASES["load_interrupted_mw"]))

    row = {
        "sync_id": str(sync_id).strip(),
        "region": raw.get("Region") or raw.get("region"),
        "date_of_incident": _parse_date(raw.get(_INCIDENT_DISPLAY_ALIASES["date_of_incident"])),
        "time_out": _parse_time(raw.get(_INCIDENT_DISPLAY_ALIASES["time_out"])),
        "location_station": raw.get(_INCIDENT_DISPLAY_ALIASES["location_station"]),
        "incident_type": raw.get(_INCIDENT_DISPLAY_ALIASES["incident_type"]),
        "description": raw.get(_INCIDENT_DISPLAY_ALIASES["description"]),
        "immediate_action_taken": raw.get(_INCIDENT_DISPLAY_ALIASES["immediate_action_taken"]),
        "affected_stations": raw.get(_INCIDENT_DISPLAY_ALIASES["affected_stations"]),
        "equipment_involved": raw.get(_INCIDENT_DISPLAY_ALIASES["equipment_involved"]),
        "load_interrupted_mw": load_value,
        "load_interrupted_status": load_status,
        "potential_impact": raw.get(_INCIDENT_DISPLAY_ALIASES["potential_impact"]),
        "root_cause": raw.get(_INCIDENT_DISPLAY_ALIASES["root_cause"]),
        "corrective_action": raw.get(_INCIDENT_DISPLAY_ALIASES["corrective_action"]),
        "date_of_closure": _parse_date(raw.get(_INCIDENT_DISPLAY_ALIASES["date_of_closure"])),
        "time_in": _parse_time(raw.get(_INCIDENT_DISPLAY_ALIASES["time_in"])),
        "status": raw.get(_INCIDENT_DISPLAY_ALIASES["status"]),
        "remarks": raw.get(_INCIDENT_DISPLAY_ALIASES["remarks"]),
        "sheet_row_number": pd.to_numeric(raw.get(_INCIDENT_DISPLAY_ALIASES["sheet_row_number"]), errors="coerce"),
        "source_sheet_id": sheet_id,
        "source_tab_name": tab_name,
    }
    if pd.isna(row["sheet_row_number"]):
        row["sheet_row_number"] = None
    else:
        row["sheet_row_number"] = int(row["sheet_row_number"])
    return row


def upsert_incident_reports(rows: list, run_id: int, region: str, sheet_id: str, tab_name: str) -> dict:
    """Bulk upsert keyed on sync_id (ON CONFLICT DO UPDATE) -- additive and
    permanent, same as upsert_outage_requests: inserts new rows, updates
    matching ones, never removes or hides anything."""
    normalized, rejected = [], []
    for i, raw in enumerate(rows):
        row = _normalize_incident_row(raw, sheet_id, tab_name)
        if row is None:
            rejected.append({"row": i, "reason": "missing sync_id"})
        elif not row["date_of_incident"]:
            rejected.append({"row": i, "reason": "missing date_of_incident"})
        else:
            normalized.append(row)

    if not normalized:
        return {"inserted": 0, "updated": 0, "rejected": rejected}

    df = pd.DataFrame(normalized)[_INCIDENT_REPORT_COLUMNS]

    engine = get_engine()
    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cur.execute("DROP TABLE IF EXISTS temp_incident_reports")
        cur.execute("""
            CREATE TEMP TABLE temp_incident_reports (
                sync_id TEXT, region TEXT, date_of_incident DATE, time_out TIME,
                location_station TEXT, incident_type TEXT, description TEXT,
                immediate_action_taken TEXT, affected_stations TEXT, equipment_involved TEXT,
                load_interrupted_mw NUMERIC, load_interrupted_status TEXT, potential_impact TEXT,
                root_cause TEXT, corrective_action TEXT, date_of_closure DATE, time_in TIME,
                status TEXT, remarks TEXT, sheet_row_number INT, source_sheet_id TEXT, source_tab_name TEXT
            )
        """)
        # de-dupe on sync_id within this batch (last one wins) -- Postgres
        # can't ON CONFLICT the same target row twice in one statement
        df = df.drop_duplicates(subset=["sync_id"], keep="last")
        buffer = StringIO(df.to_csv(index=False))
        next(buffer)  # skip header
        cur.copy_expert("COPY temp_incident_reports FROM STDIN WITH CSV", buffer)

        update_cols = [c for c in _INCIDENT_REPORT_COLUMNS if c != "sync_id"]
        cur.execute(f"""
            INSERT INTO incident_reports ({", ".join(_INCIDENT_REPORT_COLUMNS)})
            SELECT {", ".join(_INCIDENT_REPORT_COLUMNS)} FROM temp_incident_reports
            WHERE sync_id IS NOT NULL
            ON CONFLICT (sync_id) DO UPDATE SET
            {", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)},
            updated_at = now()
            RETURNING (xmax = 0) AS inserted
        """)
        results = cur.fetchall()
        inserted = sum(1 for r in results if r[0])
        updated = len(results) - inserted
        cur.execute("DROP TABLE temp_incident_reports")
        raw_conn.commit()
        return {"inserted": inserted, "updated": updated, "rejected": rejected}
    finally:
        raw_conn.close()


def read_incident_reports(region: str | None = None, month: str | None = None) -> pd.DataFrame:
    """Every incident report on record, optionally region- and month-scoped.
    `month` is 'YYYY-MM' matched against date_of_incident."""
    engine = get_engine()
    clauses = []
    params: dict = {}
    if region:
        clauses.append("region = :region")
        params["region"] = region
    if month:
        clauses.append("to_char(date_of_incident, 'YYYY-MM') = :month")
        params["month"] = month
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = text(f"""
        SELECT * FROM incident_reports
        {where}
        ORDER BY date_of_incident DESC, time_out DESC NULLS LAST
    """)
    return pd.read_sql_query(query, engine, params=params)


def list_available_incident_months(region: str | None = None) -> list:
    engine = get_engine()
    where = "WHERE region = :region" if region else ""
    query = text(f"""
        SELECT DISTINCT to_char(date_of_incident, 'YYYY-MM') AS ym
        FROM incident_reports
        {where}
        ORDER BY ym DESC
    """)
    params = {"region": region} if region else {}
    with engine.connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [r[0] for r in rows if r[0]]


# -----------------------------
# DAILY MAX/MIN
# -----------------------------

_DAILY_MAX_MIN_COLUMNS = [
    "sync_id", "region", "reading_date",
    "max_load_mw", "max_load_status", "max_load_time",
    "max_freq_hz", "max_freq_status", "max_freq_time",
    "max_voltage_kv", "max_voltage_status", "max_voltage_time",
    "min_load_mw", "min_load_status", "min_load_time",
    "min_freq_hz", "min_freq_status", "min_freq_time",
    "min_voltage_kv", "min_voltage_status", "min_voltage_time",
    "source_sheet_id", "source_tab_name",
]

# The sheet's own header row repeats "TIME (HRS)" six times (once per
# max/min load, frequency, and voltage), so unlike outage_request/
# incident_report this can't be matched by header text -- Apps Script
# reads it by fixed column position instead and sends these distinct,
# synthetic JSON keys.
_DAILY_MAX_MIN_KEYS = {
    "reading_date": "DATE",
    "max_load_mw": "MAX_LOAD_MW", "max_load_time": "MAX_LOAD_TIME",
    "max_freq_hz": "MAX_FREQ_HZ", "max_freq_time": "MAX_FREQ_TIME",
    "max_voltage_kv": "MAX_VOLTAGE_KV", "max_voltage_time": "MAX_VOLTAGE_TIME",
    "min_load_mw": "MIN_LOAD_MW", "min_load_time": "MIN_LOAD_TIME",
    "min_freq_hz": "MIN_FREQ_HZ", "min_freq_time": "MIN_FREQ_TIME",
    "min_voltage_kv": "MIN_VOLTAGE_KV", "min_voltage_time": "MIN_VOLTAGE_TIME",
}
_DAILY_MAX_MIN_NUMERIC_FIELDS = [
    "max_load_mw", "max_freq_hz", "max_voltage_kv",
    "min_load_mw", "min_freq_hz", "min_voltage_kv",
]
_DAILY_MAX_MIN_TIME_FIELDS = [
    "max_load_time", "max_freq_time", "max_voltage_time",
    "min_load_time", "min_freq_time", "min_voltage_time",
]


def _normalize_daily_max_min_row(raw: dict, sheet_id: str, tab_name: str) -> dict | None:
    """Map one raw JSON row (keyed by the synthetic field names above,
    not sheet header text) into the daily_max_min column shape. Returns
    None if missing sync_id."""
    sync_id = raw.get("sync_id") or raw.get("Sync ID")
    if not sync_id:
        return None

    row = {
        "sync_id": str(sync_id).strip(),
        "region": raw.get("Region") or raw.get("region"),
        "reading_date": _parse_date(raw.get(_DAILY_MAX_MIN_KEYS["reading_date"])),
        "source_sheet_id": sheet_id,
        "source_tab_name": tab_name,
    }
    for field in _DAILY_MAX_MIN_NUMERIC_FIELDS:
        value, status = _parse_numeric_with_status(raw.get(_DAILY_MAX_MIN_KEYS[field]))
        row[field] = value
        row[f"{field.rsplit('_', 1)[0]}_status"] = status
    for field in _DAILY_MAX_MIN_TIME_FIELDS:
        row[field] = _parse_time(raw.get(_DAILY_MAX_MIN_KEYS[field]))
    return row


def upsert_daily_max_min(rows: list, run_id: int, region: str, sheet_id: str, tab_name: str) -> dict:
    """Bulk upsert keyed on sync_id (ON CONFLICT DO UPDATE) -- additive and
    permanent, same as the other two report types."""
    normalized, rejected = [], []
    for i, raw in enumerate(rows):
        row = _normalize_daily_max_min_row(raw, sheet_id, tab_name)
        if row is None:
            rejected.append({"row": i, "reason": "missing sync_id"})
        elif not row["reading_date"]:
            rejected.append({"row": i, "reason": "missing reading_date"})
        else:
            normalized.append(row)

    if not normalized:
        return {"inserted": 0, "updated": 0, "rejected": rejected}

    df = pd.DataFrame(normalized)[_DAILY_MAX_MIN_COLUMNS]

    engine = get_engine()
    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cur.execute("DROP TABLE IF EXISTS temp_daily_max_min")
        cur.execute("""
            CREATE TEMP TABLE temp_daily_max_min (
                sync_id TEXT, region TEXT, reading_date DATE,
                max_load_mw NUMERIC, max_load_status TEXT, max_load_time TIME,
                max_freq_hz NUMERIC, max_freq_status TEXT, max_freq_time TIME,
                max_voltage_kv NUMERIC, max_voltage_status TEXT, max_voltage_time TIME,
                min_load_mw NUMERIC, min_load_status TEXT, min_load_time TIME,
                min_freq_hz NUMERIC, min_freq_status TEXT, min_freq_time TIME,
                min_voltage_kv NUMERIC, min_voltage_status TEXT, min_voltage_time TIME,
                source_sheet_id TEXT, source_tab_name TEXT
            )
        """)
        # de-dupe on sync_id within this batch (last one wins) -- Postgres
        # can't ON CONFLICT the same target row twice in one statement
        df = df.drop_duplicates(subset=["sync_id"], keep="last")
        buffer = StringIO(df.to_csv(index=False))
        next(buffer)  # skip header
        cur.copy_expert("COPY temp_daily_max_min FROM STDIN WITH CSV", buffer)

        update_cols = [c for c in _DAILY_MAX_MIN_COLUMNS if c != "sync_id"]
        cur.execute(f"""
            INSERT INTO daily_max_min ({", ".join(_DAILY_MAX_MIN_COLUMNS)})
            SELECT {", ".join(_DAILY_MAX_MIN_COLUMNS)} FROM temp_daily_max_min
            WHERE sync_id IS NOT NULL
            ON CONFLICT (sync_id) DO UPDATE SET
            {", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)},
            updated_at = now()
            RETURNING (xmax = 0) AS inserted
        """)
        results = cur.fetchall()
        inserted = sum(1 for r in results if r[0])
        updated = len(results) - inserted
        cur.execute("DROP TABLE temp_daily_max_min")
        raw_conn.commit()
        return {"inserted": inserted, "updated": updated, "rejected": rejected}
    finally:
        raw_conn.close()


def read_daily_max_min(region: str | None = None, month: str | None = None) -> pd.DataFrame:
    """Every daily max/min reading on record, optionally region- and
    month-scoped. `month` is 'YYYY-MM' matched against reading_date."""
    engine = get_engine()
    clauses = []
    params: dict = {}
    if region:
        clauses.append("region = :region")
        params["region"] = region
    if month:
        clauses.append("to_char(reading_date, 'YYYY-MM') = :month")
        params["month"] = month
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = text(f"""
        SELECT * FROM daily_max_min
        {where}
        ORDER BY reading_date DESC
    """)
    return pd.read_sql_query(query, engine, params=params)


def list_available_daily_max_min_months(region: str | None = None) -> list:
    engine = get_engine()
    where = "WHERE region = :region" if region else ""
    query = text(f"""
        SELECT DISTINCT to_char(reading_date, 'YYYY-MM') AS ym
        FROM daily_max_min
        {where}
        ORDER BY ym DESC
    """)
    params = {"region": region} if region else {}
    with engine.connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [r[0] for r in rows if r[0]]
