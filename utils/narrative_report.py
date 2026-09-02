"""Narrative Daily Outage Report generator (33kV feeder app).

Builds the "short story" style daily report requested by the user -- one
prose sentence per event, region/station groupings, ranked highlights, and
a "still open" carry-over section -- entirely from templates/rules, no LLM
(validated by manually reproducing a real TCC PDF bullet from structured
fields before committing to this approach). Deliberately independent from
TCN-Outage-Manager-main/narrative_report.py, which does the same job for
330kV/132kV data using that app's own column names.
"""
import pandas as pd


def _fmt_time(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none", "nat", "") else text[:5]


def _fmt_load(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    try:
        return f"{float(value):.1f}MW"
    except (TypeError, ValueError):
        return ""


def _clean(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none", "nat", "") else text


def _is_still_open(row) -> bool:
    return not _clean(row.get("date_on")) or not _clean(row.get("time_on"))


def format_event_bullet(row) -> str:
    """One narrative sentence for a single outage event/row.

    Wording depends on outage_class: a Forced outage is an unplanned
    protection trip ("tripped due to X... Load lost: Y"), while an
    Emergency or Planned outage is a deliberate switching action ("was
    opened due to X... Load interrupted: Y") -- these are not
    interchangeable in TCN's own reporting convention.
    """
    still_open = _is_still_open(row)
    time_off = _fmt_time(row.get("time_off"))
    time_on = _fmt_time(row.get("time_on"))

    if time_off and time_on and not still_open:
        time_clause = f"{time_off}–{time_on} hrs"
    elif time_off:
        time_clause = f"{time_off} hrs"
    else:
        time_clause = ""

    station = _clean(row.get("station"))
    feeder = _clean(row.get("feeder_33kv"))
    line_label = " – ".join(x for x in [station, feeder] if x) or "A feeder"

    is_forced = _clean(row.get("outage_class")).lower() == "forced"
    cause = _clean(row.get("event_indication"))
    if is_forced:
        cause_clause = f"tripped due to {cause}" if cause else "tripped"
    else:
        cause_clause = f"was opened due to {cause}" if cause else "was opened"

    lead = f"{time_clause}: {line_label} {cause_clause}." if time_clause else f"{line_label} {cause_clause}."
    clauses = [lead]

    load = _fmt_load(row.get("last_load"))
    if load:
        load_label = "Load lost" if is_forced else "Load interrupted"
        clauses.append(f"{load_label}: {load}.")

    weather = _clean(row.get("weather_condition"))
    if weather:
        clauses.append(f"{weather} weather.")

    remarks = _clean(row.get("remarks"))
    if remarks:
        clauses.append(remarks if remarks.endswith((".", "!", "?")) else f"{remarks}.")

    if still_open:
        clauses.append("Still out at time of report.")
    else:
        officer = _clean(row.get("officer_confirming_restoration"))
        if officer:
            clauses.append(f"Restored — confirmed by {officer}.")

    return " ".join(clauses)


def build_region_sections(df: pd.DataFrame) -> dict:
    """Group events into {region: {station: [bullet, ...]}}, station/time ordered."""
    sections: dict = {}
    if df.empty:
        return sections
    df = df.sort_values(["region", "station", "time_off"])
    for region, region_df in df.groupby("region", dropna=False):
        region_name = _clean(region) or "Unassigned Region"
        stations: dict = {}
        for station, station_df in region_df.groupby("station", dropna=False):
            station_name = _clean(station) or "Unnamed Station"
            stations[station_name] = [format_event_bullet(r) for _, r in station_df.iterrows()]
        sections[region_name] = stations
    return sections


def score_notability(row) -> float:
    """Rank events for the highlights section -- bigger load lost, longer
    duration, and still-open events score higher, since those are what a
    reader needs to see first."""
    score = 0.0
    try:
        load = float(row.get("last_load"))
        if not pd.isna(load):
            score += load
    except (TypeError, ValueError):
        pass

    duration = row.get("duration_outage")
    try:
        duration_val = float(duration)
        if not pd.isna(duration_val):
            score += duration_val * 2
    except (TypeError, ValueError):
        if _clean(duration):
            score += 5

    if _is_still_open(row):
        score += 100

    if _clean(row.get("outage_class")).lower() in ("forced", "emergency"):
        score += 10

    return score


def build_highlights(df: pd.DataFrame, top_n: int = 4) -> list:
    if df.empty:
        return []
    scored = df.copy()
    scored["_score"] = scored.apply(score_notability, axis=1)
    scored = scored.sort_values("_score", ascending=False).head(top_n)
    return [format_event_bullet(r) for _, r in scored.iterrows()]


def _dominant_issue(df: pd.DataFrame) -> str:
    if df.empty:
        return "No significant system issues reported for this date."
    causes = df["event_indication"].dropna().astype(str).str.strip()
    causes = causes[causes != ""]
    if causes.empty:
        return "No significant system issues reported for this date."
    counts = causes.value_counts()
    top_cause, count = counts.index[0], int(counts.iloc[0])
    return f"{top_cause} was the most recurring issue, accounting for {count} of {len(df)} event(s) on this date."


def _grid_status_at_close(open_df: pd.DataFrame) -> str:
    if open_df.empty:
        return "All outages recorded had been restored by the close of this report."
    return f"{len(open_df)} outage(s) remained open at the close of this report, carried forward until restoration."


def build_at_a_glance(df: pd.DataFrame, open_df: pd.DataFrame) -> dict:
    class_counts = {}
    regions_covered = []
    if not df.empty:
        class_counts = df["outage_class"].fillna("Unclassified").value_counts().to_dict()
        regions_covered = sorted({_clean(r) for r in df["region"] if _clean(r)})

    return {
        "total_events": len(df),
        "class_counts": class_counts,
        "regions_covered": regions_covered,
        "still_open_count": len(open_df),
        "lowest_frequency": "N/A — not currently recorded",
        "new_assets_energised": "N/A — not currently recorded",
        "dominant_issue": _dominant_issue(df),
        "grid_status_at_close": _grid_status_at_close(open_df),
    }


def build_still_open_section(open_df: pd.DataFrame) -> list:
    if open_df.empty:
        return []
    open_df = open_df.sort_values(["date_off", "time_off"])
    lines = []
    for _, row in open_df.iterrows():
        station = _clean(row.get("station"))
        feeder = _clean(row.get("feeder_33kv"))
        label = " – ".join(x for x in [station, feeder] if x) or "A feeder"
        date_off = _clean(row.get("date_off"))
        time_off = _fmt_time(row.get("time_off"))
        since = f"{date_off} {time_off}".strip()
        cause = _clean(row.get("event_indication"))
        cause_clause = f" due to {cause}" if cause else ""
        lines.append(f"{label} has been out since {since}{cause_clause}.")
    return lines
