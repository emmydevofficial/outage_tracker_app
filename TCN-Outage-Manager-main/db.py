"""Postgres storage for TCN Grid Outage Manager (330kV/132kV).

Deliberately separate from outage_tracker-main/utils/db.py -- this app has
its own database (DATABASE_330_URL) so the two apps' data stay fully
independent, matching the two apps being fully independent deployments.

Two tables: `users` (accounts) and `outages` (every 330/132kV outage
record). Both replace what used to be users.json and
TCN_330kV_132kV_Outages_Compiled.xlsx -- see the migration plan for why.
"""
import os
from io import StringIO

import bcrypt
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

_DATE_COLS = ("date_off", "date_on")


def _parse_date(value):
    """Parse a date value the same way load_data() does (dayfirst=True) --
    Date_Off/Date_On arrive as 'DD/MM/YYYY' strings from the report form and
    uploaded workbooks. Passing that string straight to a Postgres DATE
    column lets the server's own (MDY) date parsing silently swap day/month,
    so it must be parsed here first, in Python, unambiguously."""
    if value is None:
        return None
    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


@st.cache_resource
def get_engine():
    url = os.getenv("DATABASE_330_URL")
    if not url:
        raise RuntimeError("DATABASE_330_URL is not set")
    return create_engine(url, pool_pre_ping=True)


# -----------------------------
# PASSWORDS
# -----------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# -----------------------------
# USERS
# -----------------------------

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
# OUTAGES
# -----------------------------

_OUTAGE_COLUMNS = [
    "region", "subregion_acc", "substation", "equipment", "date_off", "hour_off", "minute_off",
    "date_on", "hour_on", "minute_on", "duration", "class", "last_load_mw", "event_indication",
    "officer_interruption", "officer_restoration", "party_responsible", "weather_condition", "remarks",
]

# maps db column -> the exact column name load_data() already expects (Excel-derived casing)
_DISPLAY_ALIASES = {
    "region": "Region", "subregion_acc": "SubRegion_ACC", "substation": "Substation",
    "equipment": "Equipment", "date_off": "Date_Off", "hour_off": "Hour_Off", "minute_off": "Minute_Off",
    "date_on": "Date_On", "hour_on": "Hour_On", "minute_on": "Minute_On", "duration": "Duration",
    "class": "Class", "last_load_mw": "Last_Load_MW", "event_indication": "Event_Indication",
    "officer_interruption": "Officer_Interruption", "officer_restoration": "Officer_Restoration",
    "party_responsible": "Party_Responsible", "weather_condition": "Weather_Condition", "remarks": "Remarks",
}


def read_outages() -> pd.DataFrame:
    """Every outage record, columns aliased to match load_data()'s existing expectations exactly."""
    engine = get_engine()
    select_list = ", ".join(f'{col} AS "{alias}"' for col, alias in _DISPLAY_ALIASES.items())
    query = text(f"SELECT {select_list} FROM outages ORDER BY date_off, hour_off, minute_off")
    return pd.read_sql_query(query, engine)


def upsert_outage(row: dict, updated_by: str | None = None) -> None:
    """Single-row insert-or-update, keyed on (substation, equipment, date_off, hour_off, minute_off)."""
    engine = get_engine()
    db_row = {col: row.get(_DISPLAY_ALIASES[col]) for col in _OUTAGE_COLUMNS}
    for col in _DATE_COLS:
        db_row[col] = _parse_date(db_row[col])
    db_row["updated_by"] = updated_by
    query = text(f"""
        INSERT INTO outages ({", ".join(_OUTAGE_COLUMNS)}, updated_by)
        VALUES ({", ".join(f":{c}" for c in _OUTAGE_COLUMNS)}, :updated_by)
        ON CONFLICT (substation, equipment, date_off, hour_off, minute_off) DO UPDATE SET
        {", ".join(f"{c} = EXCLUDED.{c}" for c in _OUTAGE_COLUMNS if c not in
                    ("substation", "equipment", "date_off", "hour_off", "minute_off"))},
        updated_by = EXCLUDED.updated_by, updated_at = now()
    """)
    with engine.begin() as conn:
        conn.execute(query, db_row)


def _bulk_upsert(df: pd.DataFrame, updated_by: str | None) -> int:
    """Shared COPY-into-temp-table + upsert routine, mirrors utils/db.py::upsert_feeder_load."""
    df = df.rename(columns={v: k for k, v in _DISPLAY_ALIASES.items()})
    for col in _OUTAGE_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[_OUTAGE_COLUMNS].copy()
    for col in _DATE_COLS:
        # dayfirst parse -> pandas datetime64 renders as unambiguous ISO
        # (YYYY-MM-DD) in the CSV the COPY below sends to Postgres
        df[col] = pd.to_datetime(df[col], dayfirst=True, errors="coerce")

    engine = get_engine()
    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cur.execute("DROP TABLE IF EXISTS temp_outages")
        cur.execute(f"""
            CREATE TEMP TABLE temp_outages (
                region TEXT, subregion_acc TEXT, substation TEXT, equipment TEXT,
                date_off DATE, hour_off SMALLINT, minute_off SMALLINT,
                date_on DATE, hour_on SMALLINT, minute_on SMALLINT,
                duration TEXT, class TEXT, last_load_mw NUMERIC, event_indication TEXT,
                officer_interruption TEXT, officer_restoration TEXT,
                party_responsible TEXT, weather_condition TEXT, remarks TEXT
            )
        """)
        buffer = StringIO(df.to_csv(index=False))
        next(buffer)  # skip header
        cur.copy_expert("COPY temp_outages FROM STDIN WITH CSV", buffer)

        update_cols = [c for c in _OUTAGE_COLUMNS if c not in
                        ("substation", "equipment", "date_off", "hour_off", "minute_off")]
        cur.execute(f"""
            INSERT INTO outages ({", ".join(_OUTAGE_COLUMNS)}, updated_by)
            SELECT {", ".join(_OUTAGE_COLUMNS)}, %s FROM temp_outages
            WHERE substation IS NOT NULL AND equipment IS NOT NULL AND date_off IS NOT NULL
            ON CONFLICT (substation, equipment, date_off, hour_off, minute_off) DO UPDATE SET
            {", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)},
            updated_by = EXCLUDED.updated_by, updated_at = now()
        """, (updated_by,))
        affected = cur.rowcount
        cur.execute("DROP TABLE temp_outages")
        raw_conn.commit()
        return affected
    finally:
        raw_conn.close()


def upsert_outages_bulk(df: pd.DataFrame, updated_by: str | None = None) -> int:
    """'Append' mode for Upload Data -- upserts every row, leaves unrelated existing rows untouched."""
    return _bulk_upsert(df, updated_by)


def replace_all_outages(df: pd.DataFrame, updated_by: str | None = None) -> int:
    """'Replace' mode for Upload Data -- wipes all existing history, then loads the given rows."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE outages"))
    return _bulk_upsert(df, updated_by)
