from typing import Tuple
import os
import pandas as pd
from sqlalchemy import create_engine, text
import streamlit as st
import pandas as pd

# if a .env file exists, load variables from it (python-dotenv)
from dotenv import load_dotenv
load_dotenv()

# new helper for writing dataframes to the database

# connection string can be provided through the DATABASE_URL environment
# variable.  This allows the same code to work locally (development) and
# in production without editing the source.  If the variable is missing we
# fall back to a sensible local default but log a warning so the user knows.
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # default is convenient for development but should be overridden in
    # staging/production environments.
    DATABASE_URL = "postgresql+psycopg2://postgres:jagrelem@localhost:5432/rcc_test"
    import warnings
    warnings.warn(
        "DATABASE_URL not set; using built-in sqlite default.\n"
        "Set the DATABASE_URL environment variable to point to your PostgreSQL instance.",
        UserWarning,
    )

# -----------------------------
# CACHE ENGINE AS RESOURCE
# -----------------------------
@st.cache_resource
def get_engine():
    return create_engine(DATABASE_URL, pool_pre_ping=True)

# -----------------------------
# CACHE DATA AS DATA (SERIALIZABLE)
# -----------------------------
time_order = [ '01:00', '02:00', '03:00', '04:00', 
              '05:00',     '06:00', '07:00', '08:00', '09:00', 
              '10:00', '11:00',     '12:00', '13:00', '14:00', '15:00', 
              '16:00', '17:00',     '18:00', '19:00', '20:00', 
              '21:00', '22:00', '23:00',     '24:00']
def order_reading_time(data):
    data['reading_time'] = pd.Categorical(data['reading_time'], categories=time_order, ordered=True)
    return data

@st.cache_data(ttl=300)
def read_feeder_load(start_date: str, end_date: str) -> pd.DataFrame:
    engine = get_engine()
    query = text("""
        SELECT reading_date, reading_time, region, area, feeder as feeder_33kv, customer, station, load_mw
        FROM feeder_33kv_load
        WHERE reading_date BETWEEN :start_date AND :end_date
        ORDER BY reading_date, reading_time
    """)
    
    data = pd.read_sql_query(query, engine, params={"start_date": start_date, "end_date": end_date})
    return order_reading_time(data)


def upsert_feeder_load(df: pd.DataFrame) -> int:
    """Bulk upsert hourly 33kV feeder load readings.

    Expects columns: reading_date, reading_time, region, area, feeder,
    customer, station, load_mw, cause (in that order isn't required, but all
    must be present). Conflict key matches the table's existing unique
    constraint (reading_date, reading_time, station, feeder) -- re-uploading
    the same date/feeder/hour updates the row in place rather than duplicating it.
    """
    cols = ["reading_date", "reading_time", "region", "area", "feeder", "customer", "station", "load_mw", "cause"]
    df = df[cols]

    engine = get_engine()
    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cur.execute("""
            DROP TABLE IF EXISTS temp_feeder_load;
            CREATE TEMP TABLE temp_feeder_load (
                reading_date DATE,
                reading_time TEXT,
                region TEXT,
                area TEXT,
                feeder TEXT,
                customer TEXT,
                station TEXT,
                load_mw NUMERIC,
                cause TEXT
            )
        """)

        from io import StringIO
        csv_buffer = df.to_csv(index=False)
        buffer = StringIO(csv_buffer)
        next(buffer)  # skip header
        cur.copy_expert("COPY temp_feeder_load FROM STDIN WITH CSV", buffer)

        cur.execute("""
            INSERT INTO feeder_33kv_load (reading_date, reading_time, region, area, feeder, customer, station, load_mw, cause)
            SELECT reading_date, reading_time, region, area, feeder, customer, station, load_mw, cause
            FROM temp_feeder_load
            ON CONFLICT (reading_date, reading_time, station, feeder)
            DO UPDATE SET
                region = EXCLUDED.region,
                area = EXCLUDED.area,
                customer = EXCLUDED.customer,
                load_mw = EXCLUDED.load_mw,
                cause = EXCLUDED.cause,
                updated_at = CURRENT_TIMESTAMP
        """)
        raw_conn.commit()
        return len(df)
    finally:
        raw_conn.close()


def upsert_line_load(df: pd.DataFrame) -> int:
    """Bulk upsert hourly 330/132kV line load readings.

    Expects columns: reading_date, reading_time, region, area,
    transmission_interface, disco, line_voltage, line_nomenclature, load_mw,
    cause. Conflict key matches the table's existing unique constraint
    (reading_date, reading_time, transmission_interface, line_nomenclature) --
    re-uploading the same date/line/hour updates the row in place rather than
    duplicating it.
    """
    cols = [
        "reading_date", "reading_time", "region", "area", "transmission_interface",
        "disco", "line_voltage", "line_nomenclature", "load_mw", "cause",
    ]
    df = df[cols]

    engine = get_engine()
    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cur.execute("""
            DROP TABLE IF EXISTS temp_line_load;
            CREATE TEMP TABLE temp_line_load (
                reading_date DATE,
                reading_time TEXT,
                region TEXT,
                area TEXT,
                transmission_interface TEXT,
                disco TEXT,
                line_voltage TEXT,
                line_nomenclature TEXT,
                load_mw NUMERIC,
                cause TEXT
            )
        """)

        from io import StringIO
        csv_buffer = df.to_csv(index=False)
        buffer = StringIO(csv_buffer)
        next(buffer)  # skip header
        cur.copy_expert("COPY temp_line_load FROM STDIN WITH CSV", buffer)

        cur.execute("""
            INSERT INTO line_load (reading_date, reading_time, region, area, transmission_interface, disco, line_voltage, line_nomenclature, load_mw, cause)
            SELECT reading_date, reading_time, region, area, transmission_interface, disco, line_voltage, line_nomenclature, load_mw, cause
            FROM temp_line_load
            ON CONFLICT (reading_date, reading_time, transmission_interface, line_nomenclature)
            DO UPDATE SET
                region = EXCLUDED.region,
                area = EXCLUDED.area,
                disco = EXCLUDED.disco,
                line_voltage = EXCLUDED.line_voltage,
                load_mw = EXCLUDED.load_mw,
                cause = EXCLUDED.cause,
                updated_at = CURRENT_TIMESTAMP
        """)
        raw_conn.commit()
        return len(df)
    finally:
        raw_conn.close()


@st.cache_data(ttl=300)
def read_line_load(start_date: str, end_date: str) -> pd.DataFrame:
    engine = get_engine()
    query = text("""
        SELECT reading_date, reading_time, region, area, transmission_interface, disco, line_voltage,
               line_nomenclature, load_mw
        FROM line_load
        WHERE reading_date BETWEEN :start_date AND :end_date
        ORDER BY reading_date, reading_time
    """)
    return pd.read_sql_query(query, engine, params={"start_date": start_date, "end_date": end_date})

@st.cache_data(ttl=300)
def read_transformer_load(start_date: str, end_date: str) -> pd.DataFrame:
    engine = get_engine()
    query = text("""
        SELECT reading_date, reading_time, region, area, station, transformer_nomenclature, load_mw
        FROM transformer_load
        WHERE reading_date BETWEEN :start_date AND :end_date
        ORDER BY reading_date, reading_time
    """)

    data = pd.read_sql_query(query, engine, params={"start_date": start_date, "end_date": end_date})
    # avoid coercing reading_time into a fixed set of categories; many times
    # include minutes or irregular values which would end up as NaN under the
    # previous order_reading_time logic.  Returning the raw dataframe preserves
    # the original time strings.
    return data


def upsert_transformer_load(df: pd.DataFrame) -> int:
    """Bulk upsert hourly transformer load readings.

    Expects columns: reading_date, reading_time, region, area, station,
    transformer_nomenclature, load_mw, cause. Conflict key matches the
    table's existing unique constraint (reading_date, reading_time, station,
    transformer_nomenclature) -- re-uploading the same date/transformer/hour
    updates the row in place rather than duplicating it.
    """
    cols = ["reading_date", "reading_time", "region", "area", "station", "transformer_nomenclature", "load_mw", "cause"]
    df = df[cols]

    engine = get_engine()
    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cur.execute("""
            DROP TABLE IF EXISTS temp_transformer_load;
            CREATE TEMP TABLE temp_transformer_load (
                reading_date DATE,
                reading_time TEXT,
                region TEXT,
                area TEXT,
                station TEXT,
                transformer_nomenclature TEXT,
                load_mw NUMERIC,
                cause TEXT
            )
        """)

        from io import StringIO
        csv_buffer = df.to_csv(index=False)
        buffer = StringIO(csv_buffer)
        next(buffer)  # skip header
        cur.copy_expert("COPY temp_transformer_load FROM STDIN WITH CSV", buffer)

        cur.execute("""
            INSERT INTO transformer_load (reading_date, reading_time, region, area, station, transformer_nomenclature, load_mw, cause)
            SELECT reading_date, reading_time, region, area, station, transformer_nomenclature, load_mw, cause
            FROM temp_transformer_load
            ON CONFLICT (reading_date, reading_time, station, transformer_nomenclature)
            DO UPDATE SET
                region = EXCLUDED.region,
                area = EXCLUDED.area,
                load_mw = EXCLUDED.load_mw,
                cause = EXCLUDED.cause,
                updated_at = CURRENT_TIMESTAMP
        """)
        raw_conn.commit()
        return len(df)
    finally:
        raw_conn.close()

@st.cache_data(ttl=300)
def read_outages(start_date: str, end_date: str) -> pd.DataFrame:
    engine = get_engine()
    query = text("""
        SELECT id, disco, region, area, station, feeder_33kv, date_off, time_off, date_on, time_on,
               duration_outage, outage_class, last_load, event_indication, party_responsible, weather_condition
        FROM outages
        WHERE date_on BETWEEN :start_date AND :end_date
        ORDER BY date_off, time_off
    """)
    return pd.read_sql_query(query, engine, params={"start_date": start_date, "end_date": end_date})

@st.cache_data(ttl=300)
def read_outages_using_date_off(start_date: str, end_date: str) -> pd.DataFrame:
    engine = get_engine()
    query = text("""
        SELECT id, disco, region, area, station, feeder_33kv, date_off, time_off, date_on, time_on,
               duration_outage, outage_class, last_load, event_indication, party_responsible, weather_condition
        FROM outages
        
        ORDER BY date_off, time_off
    """)
    return pd.read_sql_query(query, engine, params={"start_date": start_date, "end_date": end_date})

# -----------------------------
# USER AUTHENTICATION HELPERS
# -----------------------------

def verify_user_password(username: str, password: str) -> bool:
    """Return True if the given username/password pair is valid."""
    import bcrypt
    engine = get_engine()
    query = text("SELECT password_hash FROM users WHERE username = :u")
    with engine.connect() as conn:
        row = conn.execute(query, {"u": username}).fetchone()
    if row is None:
        return False
    stored_hash = row[0]
    return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))


def get_user_role(username: str) -> str | None:
    """Return the role for the given username, or None if not found."""
    engine = get_engine()
    query = text("SELECT user_role FROM users WHERE username = :u")
    with engine.connect() as conn:
        row = conn.execute(query, {"u": username}).fetchone()
    return row[0] if row else None


def get_user_role_and_region(username: str) -> tuple[str | None, str | None]:
    """Return (role, region) for the given username in a single query.

    region is None for super_admin (and if the user isn't found).
    """
    engine = get_engine()
    query = text("SELECT user_role, region FROM users WHERE username = :u")
    with engine.connect() as conn:
        row = conn.execute(query, {"u": username}).fetchone()
    return (row[0], row[1]) if row else (None, None)


# -----------------------------
# USER MANAGEMENT (Super Admin)
# -----------------------------

def list_users() -> pd.DataFrame:
    """Return every user with their role, region, and creation date."""
    engine = get_engine()
    query = text("""
        SELECT username, user_role, region, created_at
        FROM users
        ORDER BY created_at
    """)
    return pd.read_sql_query(query, engine)


def count_super_admins() -> int:
    engine = get_engine()
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM users WHERE user_role = 'super_admin'")).scalar()


def create_user(username: str, password_hash: str, role: str, region: str | None) -> None:
    """Insert a new user. Raises on duplicate username (DB primary key)."""
    engine = get_engine()
    query = text("""
        INSERT INTO users (username, password_hash, user_role, region)
        VALUES (:u, :p, :r, :region)
    """)
    with engine.begin() as conn:
        conn.execute(query, {"u": username, "p": password_hash, "r": role, "region": region})


def update_user(username: str, *, role: str | None = None, region: str | None = None,
                 password_hash: str | None = None, region_explicit: bool = False) -> None:
    """Update an existing user's role/region/password.

    role/password_hash are only applied when provided. region is only applied
    when region_explicit=True (so callers can distinguish "leave region
    unchanged" from "set region to NULL", which matters when switching a user
    to super_admin).
    """
    engine = get_engine()
    sets = []
    params = {"u": username}
    if role is not None:
        sets.append("user_role = :role")
        params["role"] = role
    if region_explicit:
        sets.append("region = :region")
        params["region"] = region
    if password_hash is not None:
        sets.append("password_hash = :password_hash")
        params["password_hash"] = password_hash
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
# DELETE OPERATIONS
# -----------------------------

def delete_outages_by_date(start_date: str, end_date: str, region: str | None = None) -> int:
    """Delete outage records where date_off falls in the given range.

    Parameters
    ----------
    start_date: str
        Date string in 'YYYY-MM-DD' format.
    end_date: str
        Date string in 'YYYY-MM-DD' format.
    region: str | None
        When provided, only rows matching this region (case-insensitive) are
        deleted -- used to confine a regional user's delete to their own
        region. None (the default) deletes across all regions, unchanged
        from prior behavior.

    Returns
    -------
    int
        Number of rows deleted.
    """
    engine = get_engine()
    sql = "DELETE FROM outages WHERE date_off BETWEEN :start_date AND :end_date"
    params = {"start_date": start_date, "end_date": end_date}
    if region:
        sql += " AND region ILIKE :region"
        params["region"] = region
    with engine.begin() as conn:
        result = conn.execute(text(sql), params)
        return result.rowcount


def _delete_load_by_date_region(table: str, start_date: str, end_date: str, region: str | None) -> int:
    """Shared implementation for the three hourly load tables below.

    All three (feeder_33kv_load, line_load, transformer_load) share the same
    reading_date/region column names, so a single parameterized DELETE covers
    all of them -- `table` is never user-supplied (always one of the three
    literals passed by the wrapper functions), so it's safe to interpolate
    directly rather than bind as a query parameter.
    """
    engine = get_engine()
    sql = f"DELETE FROM {table} WHERE reading_date BETWEEN :start_date AND :end_date"
    params = {"start_date": start_date, "end_date": end_date}
    if region:
        sql += " AND region ILIKE :region"
        params["region"] = region
    with engine.begin() as conn:
        result = conn.execute(text(sql), params)
        return result.rowcount


def delete_feeder_load_by_date(start_date: str, end_date: str, region: str | None = None) -> int:
    """Delete feeder_33kv_load rows in the given reading_date range.

    region: None deletes across all regions (Super Admin "All Regions");
    otherwise only rows matching that region (case-insensitive) are removed.
    """
    return _delete_load_by_date_region("feeder_33kv_load", start_date, end_date, region)


def delete_line_load_by_date(start_date: str, end_date: str, region: str | None = None) -> int:
    """Delete line_load rows in the given reading_date range (see delete_feeder_load_by_date)."""
    return _delete_load_by_date_region("line_load", start_date, end_date, region)


def delete_transformer_load_by_date(start_date: str, end_date: str, region: str | None = None) -> int:
    """Delete transformer_load rows in the given reading_date range (see delete_feeder_load_by_date)."""
    return _delete_load_by_date_region("transformer_load", start_date, end_date, region)


def truncate_outages() -> None:
    """Truncate the outages table and reset the id sequence.

    This is a destructive, irreversible operation that removes ALL records
    and resets the auto-increment id back to 1.  Only admins should be
    allowed to call this.
    """
    engine = get_engine()
    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cur.execute("TRUNCATE TABLE outages RESTART IDENTITY CASCADE;")
        raw_conn.commit()
    finally:
        raw_conn.close()


def insert_outages(df: pd.DataFrame) -> None:
    """Insert outage records contained in ``df`` into the permanent table.

    Internally this writes the dataframe to a CSV stream and uses a
    ``COPY`` into a temporary table.  An ``ON CONFLICT`` clause ensures that
    existing rows (keyed by station/feeder/date_off/time_off) are updated when
    certain fields differ.  This mirrors ``insert_outages_from_csv`` but
    operates on an already-loaded dataframe.
    """
    engine = get_engine()
    cols = [
        "disco", "region", "area", "station", "feeder_33kv", "date_off", "time_off",
        "date_on", "time_on", "duration_outage", "outage_class", "last_load",
        "event_indication", "party_responsible", "officer_confirming_interruption",
        "officer_confirming_restoration", "weather_condition", "remarks"
    ]

    # write df to csv buffer
    csv_buffer = df.to_csv(index=False)
    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cur.execute("""
            DROP TABLE IF EXISTS temp_outages;
            CREATE TEMP TABLE temp_outages (
                disco TEXT,
                region TEXT,
                area TEXT,
                station TEXT,
                feeder_33kv TEXT,
                date_off DATE,
                time_off TIME,
                date_on DATE,
                time_on TIME,
                duration_outage TEXT,
                outage_class TEXT,
                last_load NUMERIC,
                event_indication TEXT,
                party_responsible TEXT,
                officer_confirming_interruption TEXT,
                officer_confirming_restoration TEXT,
                weather_condition TEXT,
                remarks TEXT
            )
        """)
        # copy from buffer
        from io import StringIO
        buffer = StringIO(csv_buffer)
        next(buffer)  # skip header
        cur.copy_expert("COPY temp_outages FROM STDIN WITH CSV", buffer)

        cur.execute("""
            WITH dedup AS (
                SELECT DISTINCT ON (station, feeder_33kv, date_off, time_off) *
                FROM temp_outages
                ORDER BY station, feeder_33kv, date_off, time_off, date_on DESC NULLS LAST, time_on DESC NULLS LAST
            )
            INSERT INTO outages (
                disco,
                region,
                area,
                station,
                feeder_33kv,
                date_off,
                time_off,
                date_on,
                time_on,
                duration_outage,
                outage_class,
                last_load,
                event_indication,
                party_responsible,
                officer_confirming_interruption,
                officer_confirming_restoration,
                weather_condition,
                remarks
            )
            SELECT
                disco,
                region,
                area,
                station,
                feeder_33kv,
                date_off,
                time_off,
                date_on,
                time_on,
                duration_outage,
                outage_class,
                last_load,
                event_indication,
                party_responsible,
                officer_confirming_interruption,
                officer_confirming_restoration,
                weather_condition,
                remarks
            FROM dedup
            ON CONFLICT (station, feeder_33kv, date_off, time_off)
            DO UPDATE SET
                date_on = EXCLUDED.date_on,
                time_on = EXCLUDED.time_on,
                duration_outage = EXCLUDED.duration_outage,
                outage_class = EXCLUDED.outage_class,
                last_load = EXCLUDED.last_load,
                event_indication = EXCLUDED.event_indication,
                party_responsible = EXCLUDED.party_responsible,
                officer_confirming_interruption = EXCLUDED.officer_confirming_interruption,
                officer_confirming_restoration = EXCLUDED.officer_confirming_restoration,
                weather_condition = EXCLUDED.weather_condition,
                remarks = EXCLUDED.remarks,
                updated_at = CURRENT_TIMESTAMP
            WHERE
                outages.date_on IS DISTINCT FROM EXCLUDED.date_on
                OR outages.time_on IS DISTINCT FROM EXCLUDED.time_on
                OR outages.duration_outage IS DISTINCT FROM EXCLUDED.duration_outage
                OR outages.last_load IS DISTINCT FROM EXCLUDED.last_load
                OR outages.remarks IS DISTINCT FROM EXCLUDED.remarks;
        """)
        raw_conn.commit()
    finally:
        raw_conn.close()



def insert_outages_from_csv(csv_path: str) -> None:
    """Efficiently load a CSV file directly into ``outages`` using COPY.

    The CSV must have a header matching the expected outage columns with
    ``time_off``/``time_on`` already computed (i.e. the output of the
    Streamlit uploader).  Rows are merged on the unique key defined by
    ``(station, feeder_33kv, date_off, time_off)`` with an update-if-changed
    conflict clause.
    """
    engine = get_engine()
    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        # create temp table
        cur.execute("""
            DROP TABLE IF EXISTS temp_outages;
            CREATE TEMP TABLE temp_outages (
                disco TEXT,
                region TEXT,
                area TEXT,
                station TEXT,
                feeder_33kv TEXT,
                date_off DATE,
                time_off TIME,
                date_on DATE,
                time_on TIME,
                duration_outage TEXT,
                outage_class TEXT,
                last_load NUMERIC,
                event_indication TEXT,
                party_responsible TEXT,
                officer_confirming_interruption TEXT,
                officer_confirming_restoration TEXT,
                weather_condition TEXT,
                remarks TEXT
            )
        """)
        with open(csv_path, 'r', encoding='utf-8') as f:
            next(f)  # skip header
            cur.copy_expert("COPY temp_outages FROM STDIN WITH CSV", f)

        cur.execute("""
            WITH dedup AS (
                SELECT DISTINCT ON (station, feeder_33kv, date_off, time_off) *
                FROM temp_outages
                ORDER BY station, feeder_33kv, date_off, time_off, date_on DESC NULLS LAST, time_on DESC NULLS LAST
            )
            INSERT INTO outages (
                disco,
                region,
                area,
                station,
                feeder_33kv,
                date_off,
                time_off,
                date_on,
                time_on,
                duration_outage,
                outage_class,
                last_load,
                event_indication,
                party_responsible,
                officer_confirming_interruption,
                officer_confirming_restoration,
                weather_condition,
                remarks
            )
            SELECT
                disco,
                region,
                area,
                station,
                feeder_33kv,
                date_off,
                time_off,
                date_on,
                time_on,
                duration_outage,
                outage_class,
                last_load,
                event_indication,
                party_responsible,
                officer_confirming_interruption,
                officer_confirming_restoration,
                weather_condition,
                remarks
            FROM dedup
            ON CONFLICT (station, feeder_33kv, date_off, time_off)
            DO UPDATE SET
                date_on = EXCLUDED.date_on,
                time_on = EXCLUDED.time_on,
                duration_outage = EXCLUDED.duration_outage,
                outage_class = EXCLUDED.outage_class,
                last_load = EXCLUDED.last_load,
                event_indication = EXCLUDED.event_indication,
                party_responsible = EXCLUDED.party_responsible,
                officer_confirming_interruption = EXCLUDED.officer_confirming_interruption,
                officer_confirming_restoration = EXCLUDED.officer_confirming_restoration,
                weather_condition = EXCLUDED.weather_condition,
                remarks = EXCLUDED.remarks,
                updated_at = CURRENT_TIMESTAMP
            WHERE
                outages.date_on IS DISTINCT FROM EXCLUDED.date_on
                OR outages.time_on IS DISTINCT FROM EXCLUDED.time_on
                OR outages.duration_outage IS DISTINCT FROM EXCLUDED.duration_outage
                OR outages.last_load IS DISTINCT FROM EXCLUDED.last_load
                OR outages.remarks IS DISTINCT FROM EXCLUDED.remarks;
        """)
        raw_conn.commit()
    finally:
        raw_conn.close()



@st.cache_data(ttl=300)
def read_tcn_sla_compliance() -> pd.DataFrame:
    """Return the TCN SLA compliance table with per-day outage limits.

    Columns: station, feeder_name, maximum_outage_hours, disco, feeder_band
    """
    engine = get_engine()
    query = text("""
        SELECT station, feeder_name, maximum_outage_hours, disco, feeder_band
        FROM tcn_sla_compliance
    """)
    return pd.read_sql_query(query, engine)


# -----------------------------
# TARIFF (outage-hour exceedance cost) HELPERS
# -----------------------------

@st.cache_data(ttl=300)
def read_tariff_settings() -> float:
    """The single global default tariff rate (Naira/kWh)."""
    engine = get_engine()
    with engine.connect() as conn:
        rate = conn.execute(text("SELECT default_rate_ngn_per_kwh FROM tariff_settings WHERE id = 1")).scalar()
    return float(rate) if rate is not None else 206.50


@st.cache_data(ttl=300)
def read_tariff_rates() -> pd.DataFrame:
    """Every explicit (disco, band) -> rate override. Columns: disco, band, rate_ngn_per_kwh, updated_at, updated_by."""
    engine = get_engine()
    query = text("""
        SELECT disco, band, rate_ngn_per_kwh, updated_at, updated_by
        FROM tariff_rates
        ORDER BY disco, band
    """)
    return pd.read_sql_query(query, engine)


@st.cache_data(ttl=300)
def list_known_discos() -> list:
    """Every disco that appears in the SLA table, for the tariff settings grid."""
    engine = get_engine()
    query = text("SELECT DISTINCT disco FROM tcn_sla_compliance WHERE disco IS NOT NULL AND disco <> '' ORDER BY disco")
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(query).fetchall()]


def update_tariff_settings(rate: float, updated_by: str | None) -> None:
    """Update the single global default tariff rate."""
    engine = get_engine()
    query = text("""
        UPDATE tariff_settings
        SET default_rate_ngn_per_kwh = :rate, updated_at = now(), updated_by = :updated_by
        WHERE id = 1
    """)
    with engine.begin() as conn:
        conn.execute(query, {"rate": rate, "updated_by": updated_by})


def upsert_tariff_rate(disco: str, band: str, rate: float, updated_by: str | None) -> None:
    """Insert or update the rate for a single (disco, band) combination."""
    engine = get_engine()
    query = text("""
        INSERT INTO tariff_rates (disco, band, rate_ngn_per_kwh, updated_at, updated_by)
        VALUES (:disco, :band, :rate, now(), :updated_by)
        ON CONFLICT (disco, band) DO UPDATE
        SET rate_ngn_per_kwh = EXCLUDED.rate_ngn_per_kwh, updated_at = now(), updated_by = EXCLUDED.updated_by
    """)
    with engine.begin() as conn:
        conn.execute(query, {"disco": disco, "band": band, "rate": rate, "updated_by": updated_by})
