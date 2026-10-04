"""Data-quality checks for an outage file before (or after) it's uploaded.

Read-only throughout: nothing here writes to the database or changes the
file. Every check returns a plain DataFrame of flagged rows for a human to
review and fix themselves -- nothing here is ever applied automatically.

Feeders are checked against tcn_sla_compliance, the same ground-truth
table the SLA Outage Reports feature reads, reusing its proven
normalization (the 0/O digit-letter corruption, voltage-token stripping,
etc. -- see reliability_report/data.py) rather than reimplementing it.
"""
from __future__ import annotations

import difflib

import pandas as pd
from sqlalchemy import text

from reliability_report.data import normalize_feeder, normalize_station
from utils.db import get_engine

FUZZY_CUTOFF = 0.72  # difflib similarity ratio below this is "not close enough to guess"


def _display_clean(name: str) -> str:
    """tcn_sla_compliance's own values are sometimes corrupted the same
    way an uploaded file's might be (e.g. Enugu's "0SSAMALA" for
    "OSSAMALA") -- a suggestion should show the engineer a clean name,
    not parrot the source table's own typo back at them. Display-only;
    never used for matching."""
    import re
    return re.sub(r"(?<=[A-Za-z])0|0(?=[A-Za-z])", "O", str(name or ""))


def load_reference_feeders() -> pd.DataFrame:
    """Every (region, disco, station, feeder) on record in
    tcn_sla_compliance, with normalized match keys added."""
    engine = get_engine()
    df = pd.read_sql_query(text(
        "SELECT region, disco, station, feeder_name, feeder_band FROM tcn_sla_compliance"), engine)
    df["station_norm"] = df["station"].map(normalize_station)
    df["feeder_norm"] = df["feeder_name"].map(normalize_feeder)
    return df


def load_known_discos() -> list[str]:
    engine = get_engine()
    with engine.connect() as con:
        rows = con.execute(text("SELECT DISTINCT disco FROM tariff_rates ORDER BY 1")).fetchall()
    return [r[0] for r in rows]


def _norm_party_disco(v) -> str:
    return " ".join(str(v or "").strip().upper().replace("-", " ").split())


def check_party_and_disco(df: pd.DataFrame) -> pd.DataFrame:
    """Flags party_responsible/disco values that aren't a clean match
    against the known-good sets once whitespace/case/hyphen differences
    are normalized -- the exact issue class behind two real bugs found
    this session (" TCN" vs "TCN", "Port-Harcourt" vs "PORT HARCOURT")."""
    known_discos = {_norm_party_disco(d) for d in load_known_discos()}
    rows = []
    for col, known, label in [("party_responsible", {"TCN", "DISCO"}, "Party Responsible"),
                               ("disco", known_discos, "DisCo")]:
        if col not in df.columns:
            continue
        raw = df[col].astype(str)
        norm = raw.map(_norm_party_disco)
        bad = ~norm.isin(known) & (raw.str.strip() != "")
        for idx in df.index[bad]:
            rows.append(dict(row=idx + 2, field=label, raw_value=df.at[idx, col],
                             issue=f"'{df.at[idx, col]}' does not match a known {label.lower()} "
                                   f"after trimming spaces/case -- check for a typo or stray whitespace"))
    return pd.DataFrame(rows)


def check_dates(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(impossible_order, open_ended). impossible_order: date_on/time_on
    earlier than date_off/time_off. open_ended: no date_on at all --
    informational, not necessarily wrong, but worth a human's attention
    given how much this exact thing already distorted a report this
    session."""
    d_off = pd.to_datetime(df.get("date_off", pd.Series(dtype="object")).astype(str) + " " +
                           df.get("time_off", pd.Series(dtype="object")).astype(str), errors="coerce")
    d_on = pd.to_datetime(df.get("date_on", pd.Series(dtype="object")).astype(str) + " " +
                          df.get("time_on", pd.Series(dtype="object")).astype(str), errors="coerce")
    impossible = d_on.notna() & d_off.notna() & (d_on < d_off)
    open_ended = d_off.notna() & d_on.isna()

    def _preview(mask, extra_cols=()):
        cols = [c for c in ("region", "station", "feeder_33kv", "date_off", "time_off", "date_on", "time_on", *extra_cols) if c in df.columns]
        out = df.loc[mask, cols].copy()
        out.insert(0, "row", df.index[mask] + 2)
        return out.reset_index(drop=True)

    return _preview(impossible), _preview(open_ended)


def check_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """Repeated (station, feeder, date_off, time_off) -- the same key
    insert_outages()'s own ON CONFLICT already treats as one row, so
    duplicates in the source file are silently merged on upload rather
    than flagged; better to show them here first."""
    key_cols = [c for c in ("station", "feeder_33kv", "date_off", "time_off") if c in df.columns]
    if len(key_cols) < 4:
        return pd.DataFrame()
    dup_mask = df.duplicated(subset=key_cols, keep=False)
    out = df.loc[dup_mask, ["region", *key_cols]].copy() if "region" in df.columns else df.loc[dup_mask, key_cols].copy()
    out.insert(0, "row", df.index[dup_mask] + 2)
    return out.sort_values(key_cols).reset_index(drop=True)


def _closest(name: str, candidates: list[str]) -> tuple[str | None, float]:
    if not candidates:
        return None, 0.0
    matches = difflib.get_close_matches(name, candidates, n=1, cutoff=FUZZY_CUTOFF)
    if not matches:
        return None, 0.0
    ratio = difflib.SequenceMatcher(None, name, matches[0]).ratio()
    return matches[0], ratio


def check_feeders(df: pd.DataFrame, use_ai: bool = False, ai_provider: str | None = None,
                   ai_api_key: str | None = None, ai_model: str | None = None) -> pd.DataFrame:
    """One row per distinct (region, station, feeder) combination found
    in the file that doesn't match tcn_sla_compliance -- a typo usually
    repeats across many outage rows, so this is reported once per
    distinct value, with how many file rows it affects."""
    ref = load_reference_feeders()
    station_to_real = dict(zip(ref["station_norm"], ref["station"]))
    stations_norm = sorted(set(ref["station_norm"]))
    feeders_by_station_norm: dict[str, list[str]] = {}
    feeder_norm_to_real: dict[tuple[str, str], str] = {}
    for r in ref.itertuples():
        feeders_by_station_norm.setdefault(r.station_norm, []).append(r.feeder_norm)
        feeder_norm_to_real[(r.station_norm, r.feeder_norm)] = r.feeder_name

    station_col = "station" if "station" in df.columns else None
    feeder_col = "feeder_33kv" if "feeder_33kv" in df.columns else None
    if not station_col or not feeder_col:
        return pd.DataFrame()

    combos = (df.groupby([c for c in ("region", station_col, feeder_col) if c in df.columns])
              .size().reset_index(name="rows"))

    ai_queue = []
    results = []
    for r in combos.itertuples():
        region = getattr(r, "region", None)
        station_raw = getattr(r, station_col)
        feeder_raw = getattr(r, feeder_col)
        st_norm = normalize_station(station_raw)
        fd_norm = normalize_feeder(feeder_raw)

        if st_norm in feeders_by_station_norm and fd_norm in feeders_by_station_norm[st_norm]:
            continue  # exact match, nothing to flag

        if st_norm in feeders_by_station_norm:
            # station is known; feeder isn't -- fuzzy match within that station's real feeders
            real_feeders_norm = feeders_by_station_norm[st_norm]
            match_norm, ratio = _closest(fd_norm, real_feeders_norm)
            if match_norm:
                results.append(dict(region=region, station=station_raw, feeder=feeder_raw, rows=r.rows,
                                    issue="possible typo in feeder name",
                                    # the station already matched -- keep the user's own (already
                                    # correct) spelling rather than echoing tcn_sla_compliance's own
                                    # raw value, which for some stations (e.g. Enugu) is itself
                                    # corrupted ("0nitsha132kV") and would look like a new typo.
                                    suggested_station=station_raw,
                                    suggested_feeder=_display_clean(feeder_norm_to_real[(st_norm, match_norm)]),
                                    source=f"fuzzy match ({ratio:.0%} similar)"))
                continue
            ai_queue.append((region, station_raw, feeder_raw, station_to_real[st_norm], real_feeders_norm, feeder_norm_to_real, st_norm))
            continue

        # station itself not recognized -- fuzzy match the station first
        match_station_norm, ratio = _closest(st_norm, stations_norm)
        if match_station_norm:
            real_feeders_norm = feeders_by_station_norm[match_station_norm]
            fmatch_norm, fratio = _closest(fd_norm, real_feeders_norm)
            results.append(dict(region=region, station=station_raw, feeder=feeder_raw, rows=r.rows,
                                issue="unrecognized station (possible typo)",
                                suggested_station=_display_clean(station_to_real[match_station_norm]),
                                suggested_feeder=(_display_clean(feeder_norm_to_real.get((match_station_norm, fmatch_norm), ""))
                                                  if fmatch_norm else ""),
                                source=f"fuzzy match ({ratio:.0%} similar)"))
        else:
            ai_queue.append((region, station_raw, feeder_raw, None, None, feeder_norm_to_real, None))

    if ai_queue and use_ai and ai_api_key:
        from utils.outage_quality_ai import ai_suggest_feeder
        for region, station_raw, feeder_raw, known_station, real_feeders_norm, _map, st_norm in ai_queue:
            candidates = (ref[ref["region"] == region] if region in set(ref["region"]) else ref)
            cand_list = sorted(set(zip(candidates["station"], candidates["feeder_name"])))[:60]
            guess = ai_suggest_feeder(station_raw, feeder_raw, region, cand_list, ai_provider, ai_api_key, ai_model)
            results.append(dict(
                region=region, station=station_raw, feeder=feeder_raw,
                rows=next(c.rows for c in combos.itertuples() if getattr(c, station_col) == station_raw and getattr(c, feeder_col) == feeder_raw),
                issue="unrecognized feeder" if known_station else "unrecognized station and feeder",
                suggested_station=_display_clean(guess[0]) if guess else "",
                suggested_feeder=_display_clean(guess[1]) if guess else "",
                source="AI guess" if guess else "no match found -- needs manual review",
            ))
    else:
        for region, station_raw, feeder_raw, known_station, real_feeders_norm, _map, st_norm in ai_queue:
            results.append(dict(
                region=region, station=station_raw, feeder=feeder_raw,
                rows=next(c.rows for c in combos.itertuples() if getattr(c, station_col) == station_raw and getattr(c, feeder_col) == feeder_raw),
                issue="unrecognized feeder" if known_station else "unrecognized station and feeder",
                suggested_station="", suggested_feeder="",
                source="no close match found -- needs manual review",
            ))

    return pd.DataFrame(results)


def check_region_mismatch(df: pd.DataFrame) -> pd.DataFrame:
    """The row's own region doesn't match the region tcn_sla_compliance
    has on record for that station -- a region typed into the wrong
    column, or a station that genuinely moved regions."""
    if "region" not in df.columns or "station" not in df.columns:
        return pd.DataFrame()
    ref = load_reference_feeders()
    station_region = dict(zip(ref["station_norm"], ref["region"]))
    rows = []
    for idx, row in df.iterrows():
        st_norm = normalize_station(row["station"])
        known_region = station_region.get(st_norm)
        if known_region and str(row["region"]).strip().upper() != str(known_region).strip().upper():
            rows.append(dict(row=idx + 2, region=row["region"], station=row["station"],
                             expected_region=known_region))
    return pd.DataFrame(rows)
