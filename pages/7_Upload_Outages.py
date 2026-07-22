"""
### FILE: pages/7_Upload_Outages.py
Utility page that allows the user to upload a CSV file containing outage
records and push the rows into the PostgreSQL ``outages`` table.  The
uploader expects the CSV to follow the layout described in the project
requirements (hour/minute columns that will be collapsed).
"""

import streamlit as st
from utils.auth import login
import pandas as pd
import numpy as np
from utils.db import insert_outages, insert_outages_from_csv

login()

st.set_page_config(page_title="Upload Outages", layout="wide")

st.title("📁 Upload Outage CSV")

upload = st.file_uploader("Choose outage CSV file", type=["csv"])

EXPECTED_COLUMNS = [
    "disco",
    "region",
    "area",
    "station",
    "feeder_33kv",
    "date_off",
    "hour_off",
    "minute_off",
    "date_on",
    "hour_on",
    "minute_on",
    "duration_outage",
    "outage_class",
    "last_load",
    "event_indication",
    "party_responsible",
    "officer_confirming_interruption",
    "officer_confirming_restoration",
    "weather_condition",
    "remarks",
]

if upload is not None:
    # try a few common encodings since uploaded files may not be utf-8
    encodings = ("utf-8", "utf-8-sig", "cp1252", "latin1")
    df = None
    last_exc = None
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
        st.error("Uploaded file is missing expected columns: %s" % ", ".join(missing))
        st.stop()

    

    # Custom time handling: safely collapse hour/minute into HH:MM and
    # leave missing values as None/NaN.
    def _assemble_time(df, hour_col, minute_col, out_col):
        # get left-of-colon and right-of-colon parts if the cell contains a hh:mm
        hour_str = df[hour_col].astype("string").str.partition(":")[0]
        minute_str = df[minute_col].astype("string").str.partition(":")[2]

        hour_num = pd.to_numeric(hour_str, errors="coerce")
        minute_num = pd.to_numeric(minute_str, errors="coerce")

        mask = hour_num.notna() & minute_num.notna()

        out = pd.Series([None] * len(df), index=df.index, dtype="object")
        if mask.any():
            hh = hour_num[mask].astype(int).astype(str).str.zfill(2)
            mm = minute_num[mask].astype(int).astype(str).str.zfill(2)
            out.loc[mask] = hh + ":" + mm

        df[out_col] = out

    _assemble_time(df, "hour_off", "minute_off", "time_off")
    _assemble_time(df, "hour_on", "minute_on", "time_on")

    # coerce dates and numeric columns
    # dayfirst=True: source files use DD/MM/YYYY (e.g. "15/07/2026"). Without this,
    # pandas infers a single MM/DD/YYYY format from the column and silently swaps
    # day/month for rows where day <= 12, and turns rows where day > 12 into NaT
    # (which then violates the outages.date_off NOT NULL constraint on insert).
    df["date_off_raw"] = df["date_off"]
    df["date_on_raw"] = df["date_on"]
    df["date_off"] = pd.to_datetime(df["date_off"], errors="coerce", dayfirst=True).dt.date
    # for date_on, handle None/empty values
    df["date_on"] = pd.to_datetime(df["date_on"], errors="coerce", dayfirst=True).dt.date
    df["last_load"] = pd.to_numeric(df["last_load"], errors="coerce")

    # final frame for insertion
    insert_df = df[
        [
            "disco",
            "region",
            "area",
            "station",
            "feeder_33kv",
            "date_off",
            "time_off",
            "date_on",
            "time_on",
            "duration_outage",
            "outage_class",
            "last_load",
            "event_indication",
            "party_responsible",
            "officer_confirming_interruption",
            "officer_confirming_restoration",
            "weather_condition",
            "remarks",
        ]
    ].copy()

    # ── Row-level validation ────────────────────────────────────────────────
    # The database rejects rows with a null date_off (and a real outage record
    # is meaningless without disco/region/station/feeder/time_off either), so
    # check every row *before* attempting the insert and explain exactly what
    # is wrong and where, instead of letting a raw DB error surface later.
    def _is_blank(series):
        return series.isna() | (series.astype(str).str.strip() == "")

    reasons = pd.Series([[] for _ in range(len(df))], index=df.index)

    date_off_bad = _is_blank(insert_df["date_off"])
    for idx in df.index[date_off_bad]:
        reasons[idx].append(f"Date Off could not be parsed (raw value: '{df.at[idx, 'date_off_raw']}')")

    time_off_bad = _is_blank(insert_df["time_off"])
    for idx in df.index[time_off_bad]:
        reasons[idx].append(
            f"Hour Off/Minute Off could not be combined into a time "
            f"(raw values: hour_off='{df.at[idx, 'hour_off']}', minute_off='{df.at[idx, 'minute_off']}')"
        )

    for col, label in [("disco", "Disco"), ("region", "Region"), ("station", "Station"), ("feeder_33kv", "Feeder")]:
        bad = _is_blank(insert_df[col])
        for idx in df.index[bad]:
            reasons[idx].append(f"Missing {label}")

    invalid_mask = reasons.apply(len).gt(0)
    valid_df = insert_df[~invalid_mask].reset_index(drop=True)

    if invalid_mask.any():
        invalid_preview = df.loc[invalid_mask, ["disco", "region", "station", "feeder_33kv", "date_off_raw", "hour_off", "minute_off", "date_on_raw"]].copy()
        invalid_preview.insert(0, "CSV row #", [i + 2 for i in df.index[invalid_mask]])  # +2: header row + 1-based
        invalid_preview["Reason"] = reasons[invalid_mask].apply("; ".join)

        st.error(
            f"⚠️ {invalid_mask.sum()} of {len(df)} row(s) have problems and will be **skipped** "
            f"if you upload now. Fix these in your source file and re-upload them separately, "
            f"or download the flagged rows below to correct and resubmit."
        )
        st.dataframe(invalid_preview, use_container_width=True)
        st.download_button(
            "Download flagged rows (CSV)",
            invalid_preview.to_csv(index=False),
            "flagged_rows.csv",
            "text/csv",
        )

    st.subheader("Preview of valid records")
    st.dataframe(valid_df.head())
    st.write(f"Valid rows ready to upload: {len(valid_df)} of {len(df)}")

    if valid_df.empty:
        st.warning("No valid rows to upload.")
        st.stop()

    # write processed dataframe to a temporary file so we can use COPY path
    import tempfile, os
    tmp_path = None
    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", mode="w", encoding="utf-8", newline="")
        # write the already-transformed dataframe rather than raw upload bytes
        valid_df.to_csv(tmp, index=False, encoding="utf-8")
        tmp.flush()
        tmp_path = tmp.name
        tmp.close()
    except Exception:
        tmp_path = None

    if st.button("Upload to database"):
        try:
            if tmp_path and os.path.exists(tmp_path):
                # prefer the faster CSV-based path when available
                insert_outages_from_csv(tmp_path)
            else:
                insert_outages(valid_df)
            st.success(f"{len(valid_df)} outage record(s) successfully inserted into database")
        except Exception as e:
            st.error(f"Error inserting records: {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
