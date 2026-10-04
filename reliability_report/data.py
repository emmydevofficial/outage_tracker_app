"""Read-only loaders for outages, the SLA table and the tariff table, plus
the name-normalization layer needed to match them reliably.

Nothing here writes to the database -- storage of report runs, overrides
and notes lives in store.py instead.
"""
from __future__ import annotations

import datetime as dt
import re

import pandas as pd
from sqlalchemy import text

from utils.db import get_engine, read_tariff_settings

TCN_SHARE = 0.30
BAND_HOURS = {"A": 4.0, "B": 8.0, "C": 12.0, "D": 16.0}
DEFAULT_BAND = "A"

# outages.disco values that are non-empty but not a real DisCo name --
# confirmed (Sept 2026 data) by cross-referencing the station against
# both tcn_sla_compliance and this station's OTHER outage rows; both
# sources agree on the DisCo in every case, so this is a lookup of a
# known fact, not a guess.
_DISCO_STATION_OVERRIDE = {
    "AKURE 132KV": "BENIN",
    "GANMO 132KV": "IBADAN",
    "ILE-IFE 132KV": "IBADAN",
    "OSHOGBO 132KV": "IBADAN",
    "ONITSHA 132KV": "ENUGU",
}
_INVALID_DISCO_VALUES = {"ELIGIBLE", "FEEDER ERROR"}
_DISCO_ALIASES = {"EEDC": "ENUGU"}


def normalize_disco(disco, station: str | None = None) -> str | None:
    """Upper-case, hyphen/space-insensitive DisCo name, or None if
    genuinely unknown (empty, or an invalid value with no station
    override on record)."""
    raw = str(disco).strip() if disco is not None else ""
    station_key = re.sub(r"\s+", " ", str(station).strip()).upper() if station else ""
    if not raw:
        return _DISCO_STATION_OVERRIDE.get(station_key)
    key = re.sub(r"[\s\-]+", " ", raw).upper()
    if key in _INVALID_DISCO_VALUES:
        return _DISCO_STATION_OVERRIDE.get(station_key)
    return _DISCO_ALIASES.get(key, key)


def normalize_station(station) -> str:
    """Voltage token stripped first (so real digits in "132"/"330" are
    never touched), then 0->O (a real, Enugu-only data-entry corruption
    in tcn_sla_compliance: "0nitsha132kV" for "Onitsha 132kV"), then every
    remaining non-letter (spaces, hyphens) is dropped so spacing/hyphen
    differences never cause a false non-match."""
    s = str(station or "").upper()
    s = re.sub(r"(330|132|33)\s*KV", "", s)
    s = s.replace("0", "O")
    return re.sub(r"[^A-Z]", "", s)


def normalize_feeder(feeder) -> str:
    s = str(feeder or "").upper()
    s = re.sub(r"(33\s*KV|FDR|FEEDER)", "", s)
    # same 0/O corruption as normalize_station(), e.g. tcn_sla_compliance's
    # "0SSAMALA"/"0B0SI"/"0GIDI" for "OSSAMALA"/"OBOSI"/"OGIDI" -- but a
    # feeder name can have a real standalone number (e.g. "FDR 10"), so
    # only a 0 directly touching a letter is corruption, not a real digit.
    s = re.sub(r"(?<=[A-Z])0|0(?=[A-Z])", "O", s)
    return re.sub(r"[^A-Z0-9]", "", s)


def read_outages(period_start: dt.date, period_end: dt.date) -> pd.DataFrame:
    """Every outage overlapping [period_start, period_end], including one
    that started before the period and is still open (or still open at
    query time) -- clipped to the period so a cross-boundary outage only
    counts the minutes actually inside it. Adds the normalized match keys
    used by every downstream metric."""
    engine = get_engine()
    query = text("""
        SELECT id, disco, region, area, station, feeder_33kv, date_off, time_off, date_on, time_on,
               outage_class, last_load, party_responsible, equipment, event_indication, remarks
        FROM outages
        WHERE date_off <= :end_date AND (date_on IS NULL OR date_on >= :start_date)
    """)
    df = pd.read_sql_query(query, engine, params={"start_date": period_start, "end_date": period_end})
    if df.empty:
        # Every column downstream code expects, even with zero rows --
        # without this, a period/scope with no matching outages at all
        # (a real case on a freshly-deployed server, or just a quiet
        # period) returns a frame missing is_open/duration_hr/etc. and
        # every caller that reads those columns crashes with a KeyError.
        # dtype matters here, not just presence: is_open must be real
        # bool so `~df["is_open"]` later produces a proper boolean mask --
        # an empty object-dtype column breaks that inversion silently and
        # collapses the next filter to zero *columns*, not just zero rows.
        extra_cols = {
            "start_ts": "datetime64[ns]", "end_ts": "datetime64[ns]", "is_open": "bool",
            "clipped_start": "datetime64[ns]", "clipped_end": "datetime64[ns]",
            "duration_hr": "float64", "load_loss_mwh": "float64",
            "disco_norm": "object", "station_norm": "object", "feeder_norm": "object",
        }
        for c, dtype in extra_cols.items():
            df[c] = pd.Series(dtype=dtype)
        return df

    # some rows have party_responsible stored with stray whitespace (e.g.
    # " TCN" instead of "TCN") -- same real issue, same fix, as
    # views/reliability_kpi_report.py.
    df["party_responsible"] = df["party_responsible"].astype(str).str.strip()

    df["last_load"] = pd.to_numeric(df["last_load"], errors="coerce")
    df["start_ts"] = pd.to_datetime(df["date_off"].astype(str) + " " + df["time_off"].astype(str), errors="coerce")
    end_ts = pd.to_datetime(df["date_on"].astype(str) + " " + df["time_on"].astype(str), errors="coerce")
    df["is_open"] = end_ts.isna()  # no restoration date/time logged at all

    lo = pd.Timestamp(period_start)
    hi = pd.Timestamp(period_end) + pd.Timedelta(days=1)  # exclusive upper bound (whole last day included)
    # Still-open rows get no duration/cost here -- matching
    # views/reliability_kpi_report.py, which only counts outages with a
    # real restoration date. An outage with nothing logged at all is a
    # data-entry gap, not a known multi-week outage, and clipping it to
    # "down for the rest of the period" wildly overstates cost. They stay
    # in this DataFrame (via is_open) so callers can still list them.
    df["end_ts"] = end_ts
    df["clipped_start"] = df["start_ts"].clip(lower=lo, upper=hi)
    df["clipped_end"] = df["end_ts"].clip(lower=lo, upper=hi)
    df["duration_hr"] = ((df["clipped_end"] - df["clipped_start"]).dt.total_seconds() / 3600.0).clip(lower=0)
    df.loc[df["is_open"], "duration_hr"] = 0.0
    df["load_loss_mwh"] = df["duration_hr"] * df["last_load"].fillna(0)

    df["disco_norm"] = df.apply(lambda r: normalize_disco(r["disco"], r["station"]), axis=1)
    df["station_norm"] = df["station"].map(normalize_station)
    df["feeder_norm"] = df["feeder_33kv"].map(normalize_feeder)
    return df


def read_sla() -> pd.DataFrame:
    engine = get_engine()
    df = pd.read_sql_query(text(
        "SELECT region, disco, station, feeder_name, feeder_band, minimum_supply_hours, maximum_outage_hours "
        "FROM tcn_sla_compliance"), engine)
    df["disco_norm"] = df["disco"].map(normalize_disco)
    df["station_norm"] = df["station"].map(normalize_station)
    df["feeder_norm"] = df["feeder_name"].map(normalize_feeder)
    band = df["feeder_band"].astype(str).str.strip().str.upper()
    df["band"] = band.where(band.isin(BAND_HOURS), "")
    return df


def read_tariff_rates() -> pd.DataFrame:
    engine = get_engine()
    df = pd.read_sql_query(text("SELECT disco, band, rate_ngn_per_kwh FROM tariff_rates"), engine)
    df["disco_norm"] = df["disco"].map(normalize_disco)
    df["band"] = df["band"].astype(str).str.strip().str.upper()
    return df


def read_default_tariff() -> float:
    return read_tariff_settings()


def sla_lookup(sla_df: pd.DataFrame) -> dict[tuple[str, str], tuple[str, str, str | None]]:
    """(station_norm, feeder_norm) -> (band, region, disco_norm). Only
    rows with a real band are kept -- a feeder missing entirely, or
    present with a blank band, both fall back to Band A (assumed) per the
    same rule, so both cases are simply "not in this dict"."""
    out = {}
    for r in sla_df.itertuples():
        if r.band:
            out[(r.station_norm, r.feeder_norm)] = (r.band, r.region, r.disco_norm)
    return out


def tariff_lookup(tariff_df: pd.DataFrame) -> dict[tuple[str | None, str], float]:
    return {(r.disco_norm, r.band): float(r.rate_ngn_per_kwh) for r in tariff_df.itertuples()}


def get_rate(disco_norm: str | None, band: str, rates: dict, default_rate: float) -> float:
    return rates.get((disco_norm, band), default_rate)
