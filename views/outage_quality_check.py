"""
### FILE: views/outage_quality_check.py
Upload an outage file and see likely data-quality problems before (or
after) it goes through the real uploader -- spelling/typo issues in
station and feeder names (checked against tcn_sla_compliance, the real
feeder list), inconsistent party/disco values, impossible date ordering,
duplicate rows, and open-ended outages with no restoration logged.

This page never writes anything -- no database insert, no change to the
uploaded file. It only lists what it finds; a person decides what to fix
and fixes it themselves, in the source file or in Upload Outages.

Deliberately independent of views/upload_outages.py (not touched, not
imported) -- this is a read-only second opinion, not a gate on the real
upload.
"""
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import outage_quality as Q
from utils.branding import page_header, one_indexed

page_header("Outage Upload — Data Quality Check", "33kV Feeder Network · Pre-Upload Review")
st.caption(
    "Upload the same outage file you'd send to Upload Outages. This checks station/feeder names against "
    "the real feeder list in the SLA table, and flags other small inconsistencies -- it never changes the "
    "file or writes anything to the database. Review and fix anything flagged in your source file yourself."
)

EXPECTED_COLUMNS = [
    "disco", "region", "area", "station", "feeder_33kv", "date_off", "hour_off", "minute_off",
    "date_on", "hour_on", "minute_on", "duration_outage", "outage_class", "last_load",
    "event_indication", "party_responsible", "officer_confirming_interruption",
    "officer_confirming_restoration", "weather_condition", "remarks",
]

upload = st.file_uploader("Choose outage file (CSV or Excel)", type=["csv", "xlsx", "xls"])

ai_provider = os.getenv("AI_PROVIDER")
ai_api_key = os.getenv("AI_API_KEY")
ai_model = os.getenv("AI_MODEL")
use_ai = st.toggle(
    "Let AI suggest a match for names nothing else can resolve" if ai_api_key else
    "Let AI suggest a match (no AI_API_KEY set in .env)",
    value=bool(ai_api_key), disabled=not ai_api_key,
    help="Only used for the handful of station/feeder names that don't match exactly or closely enough to "
         "guess on their own. The AI only ever picks from the real feeders on record, or says none of them fit.",
)

if upload is not None:
    if upload.name.lower().endswith((".xlsx", ".xls")):
        try:
            df = pd.read_excel(upload)
        except Exception as e:
            st.error(f"Failed to read Excel file: {e}")
            st.stop()
    else:
        encodings = ("utf-8", "utf-8-sig", "cp1252", "latin1")
        df, last_exc = None, None
        for enc in encodings:
            try:
                upload.seek(0)
                df = pd.read_csv(upload, encoding=enc)
                break
            except Exception as exc:
                last_exc = exc
        if df is None:
            st.error(f"Failed to read CSV: {last_exc}")
            st.stop()

    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    if missing:
        st.warning(f"File is missing expected column(s): {', '.join(missing)} -- checks that need them will be skipped.")

    # the real uploader assembles time_off/time_on from hour/minute columns;
    # this page only needs them for the date-order check, so a light best-effort
    # combine is enough here (no insert, so no need for the full logic).
    if "hour_off" in df.columns and "minute_off" in df.columns and "time_off" not in df.columns:
        df["time_off"] = df["hour_off"].astype(str).str.zfill(2) + ":" + df["minute_off"].astype(str).str.zfill(2)
    if "hour_on" in df.columns and "minute_on" in df.columns and "time_on" not in df.columns:
        df["time_on"] = df["hour_on"].astype(str).str.zfill(2) + ":" + df["minute_on"].astype(str).str.zfill(2)

    st.write(f"**{len(df)} row(s) read.** Running checks...")

    with st.spinner("Checking station/feeder names against the SLA feeder list..."):
        feeder_issues = Q.check_feeders(df, use_ai=use_ai, ai_provider=ai_provider, ai_api_key=ai_api_key, ai_model=ai_model)
    party_disco_issues = Q.check_party_and_disco(df)
    impossible_dates, open_ended = Q.check_dates(df)
    duplicates = Q.check_duplicates(df)
    region_mismatch = Q.check_region_mismatch(df)

    total_issues = sum(len(x) for x in (feeder_issues, party_disco_issues, impossible_dates, duplicates, region_mismatch))
    if total_issues == 0 and open_ended.empty:
        st.success("No issues found.")
    elif total_issues == 0:
        st.success("No issues found, aside from the open-ended outages noted below.")
    else:
        st.warning(f"{total_issues} issue(s) found across the checks below (plus any open-ended outages noted separately).")

    with st.expander(f"Station / feeder name issues ({len(feeder_issues)})", expanded=not feeder_issues.empty):
        if feeder_issues.empty:
            st.write("No unrecognized or likely-mistyped station/feeder names.")
        else:
            st.dataframe(one_indexed(feeder_issues), use_container_width=True)

    with st.expander(f"Party / DisCo value issues ({len(party_disco_issues)})", expanded=not party_disco_issues.empty):
        if party_disco_issues.empty:
            st.write("No unrecognized party_responsible/disco values.")
        else:
            st.dataframe(one_indexed(party_disco_issues), use_container_width=True)

    with st.expander(f"Impossible date/time order ({len(impossible_dates)})", expanded=not impossible_dates.empty):
        if impossible_dates.empty:
            st.write("No rows where the restoration time is before the interruption time.")
        else:
            st.dataframe(one_indexed(impossible_dates), use_container_width=True)

    with st.expander(f"Duplicate rows ({len(duplicates)})", expanded=not duplicates.empty):
        if duplicates.empty:
            st.write("No duplicate (station, feeder, date off, time off) combinations.")
        else:
            st.dataframe(one_indexed(duplicates), use_container_width=True)

    with st.expander(f"Region mismatch ({len(region_mismatch)})", expanded=not region_mismatch.empty):
        if region_mismatch.empty:
            st.write("No rows where the region doesn't match the SLA table's record for that station.")
        else:
            st.dataframe(one_indexed(region_mismatch), use_container_width=True)

    with st.expander(f"Open-ended outages, no restoration logged ({len(open_ended)})", expanded=False):
        st.caption("Not necessarily wrong -- a real outage may still be ongoing. Listed for awareness only.")
        if open_ended.empty:
            st.write("None.")
        else:
            st.dataframe(one_indexed(open_ended), use_container_width=True)

    all_flagged = []
    for name, d in [("feeder", feeder_issues), ("party_disco", party_disco_issues),
                    ("impossible_dates", impossible_dates), ("duplicates", duplicates),
                    ("region_mismatch", region_mismatch), ("open_ended", open_ended)]:
        if not d.empty:
            d2 = d.copy()
            d2.insert(0, "check", name)
            all_flagged.append(d2)
    if all_flagged:
        combined = pd.concat(all_flagged, ignore_index=True, sort=False)
        st.download_button("Download all flagged rows (CSV)", combined.to_csv(index=False),
                           f"quality_check_{upload.name}.csv", "text/csv")
