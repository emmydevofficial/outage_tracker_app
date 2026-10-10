"""
### FILE: views/upload_feeder_forecast.py
Upload the daily regional feeder workbook(s) -- one file per region per
day -- that feed the forecast-based outage loss and daily feeder energy
analysis. Each file is validated and saved independently, so one bad
file never blocks the others. Region/date pause-and-confirm per
utils/daily_workbook_rules.py section 3a; everything else (layout,
row/cell rules) per feeder_forecast/parser.py.

Not touching views/upload_outages.py or the outages table at all -- a
completely separate upload path and a completely new set of tables.
"""
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openpyxl
from rapidfuzz import process as fuzz_process

from feeder_forecast.parser import parse_control_sheet, parse_workbook
from feeder_forecast import store as ff_store
from utils.auth import current_region, is_super_admin
from utils.activity_log import log_activity
from utils.branding import page_header, one_indexed
from utils.daily_workbook_rules import rules_by_severity
from utils.db import get_engine

page_header("Upload Daily Feeder Workbook", "33kV Feeder Network · Forecast & Daily Energy Data")
st.caption(
    "Upload the daily regional workbook(s) used for forecast-based outage loss and daily feeder energy "
    "analysis. Each file is checked and saved on its own -- one bad file doesn't block the others."
)

engine = get_engine()
user = st.session_state.get("username")

with st.expander("Template rules"):
    by_sev = rules_by_severity()
    labels = {"reject": "File rejected entirely", "pause": "Paused for your confirmation",
              "row_skip": "That row is skipped, rest of the file saved", "cell_warning": "Cell flagged, row still saved"}
    for sev in ("reject", "pause", "row_skip", "cell_warning"):
        st.markdown(f"**{labels[sev]}**")
        for r in by_sev[sev]:
            st.caption(f"`{r.code}` {r.title} -- {r.description}")

uploads = st.file_uploader("Choose daily feeder workbook(s) (.xlsx)", type=["xlsx"], accept_multiple_files=True)

if "ffc_state" not in st.session_state:
    st.session_state["ffc_state"] = {}  # filename -> dict(tmp_path, choice, parsed, resolved_region, resolved_date, ...)

if uploads:
    tmp_dir = Path(tempfile.mkdtemp())
    pending_pause, pending_r14, ready, rejected = [], [], [], []

    for u in uploads:
        fstate = st.session_state["ffc_state"].setdefault(u.name, {})
        if "tmp_path" not in fstate:
            p = tmp_dir / u.name
            p.write_bytes(u.getbuffer())
            fstate["tmp_path"] = str(p)
        tmp_path = fstate["tmp_path"]

        if "control" not in fstate:
            try:
                wb = openpyxl.load_workbook(tmp_path, data_only=True)
            except Exception as e:
                fstate["control"] = None
                fstate["reject"] = f"R01: could not open file ({type(e).__name__})"
                rejected.append((u.name, fstate["reject"]))
                continue
            if "Control" not in wb.sheetnames:
                fstate["control"] = None
                fstate["reject"] = 'R03: sheet "Control" is missing'
                rejected.append((u.name, fstate["reject"]))
                continue
            fstate["control"] = parse_control_sheet(wb, u.name)

        ctrl = fstate["control"]
        if ctrl is None:
            rejected.append((u.name, fstate["reject"]))
            continue
        if ctrl.status == "reject":
            rejected.append((u.name, f"{ctrl.reject_code}: {ctrl.reject_message}"))
            continue

        if ctrl.status == "pause" and "choice" not in fstate:
            pending_pause.append((u.name, ctrl, fstate))
            continue

        region = fstate.get("choice", {}).get("region", ctrl.region)
        date = fstate.get("choice", {}).get("date", ctrl.date)
        region_source = fstate.get("choice", {}).get("region_source", ctrl.region_source or "sheet")
        date_source = fstate.get("choice", {}).get("date_source", ctrl.date_source or "sheet")

        if not is_super_admin() and region != current_region():
            rejected.append((u.name, f"R09: you are not allowed to upload data for {region} (your region: {current_region()})"))
            continue

        if "parsed" not in fstate:
            fstate["parsed"] = parse_workbook(tmp_path, region, date, engine)
            fstate["region"], fstate["date"] = region, date
            fstate["region_source"], fstate["date_source"] = region_source, date_source
        result = fstate["parsed"]
        if result.status == "reject":
            rejected.append((u.name, f"{result.reject_code}: {result.reject_message}"))
            continue
        if result.status == "pause" and not fstate.get("r14_confirmed"):
            pending_r14.append((u.name, result, fstate))
            continue
        ready.append((u.name, fstate))

    if pending_pause:
        st.subheader(f"Needs confirmation ({len(pending_pause)})")
        for fname, ctrl, fstate in pending_pause:
            with st.container(border=True):
                st.write(f"**{fname}**")
                st.caption(ctrl.pause_message)
                c1, c2, c3 = st.columns(3)
                if ctrl.pause_code == "R05":
                    if c1.button("Use the sheet value", key=f"sheet_{fname}"):
                        fstate["choice"] = dict(
                            region=ctrl.sheet_region or ctrl.region, region_source="sheet",
                            date=ctrl.sheet_date or ctrl.date, date_source="sheet")
                        st.rerun()
                    if c2.button("Use the filename value", key=f"fname_{fname}"):
                        fstate["choice"] = dict(
                            region=ctrl.filename_region or ctrl.region, region_source="filename",
                            date=ctrl.filename_date or ctrl.date, date_source="filename_confirmed")
                        st.rerun()
                    if c3.button("Cancel upload", key=f"cancel_{fname}"):
                        fstate["reject"] = "Cancelled by uploader"
                        fstate["control"] = None
                        st.rerun()
                else:  # R13
                    if c1.button(f"Use {ctrl.filename_date}", key=f"usefn_{fname}"):
                        fstate["choice"] = dict(
                            region=ctrl.region, region_source=ctrl.region_source or "sheet",
                            date=ctrl.filename_date, date_source="filename_confirmed")
                        st.rerun()
                    if c2.button("Cancel upload", key=f"cancel_{fname}"):
                        fstate["reject"] = "Cancelled by uploader"
                        fstate["control"] = None
                        st.rerun()

    if pending_r14:
        st.subheader(f"Stale/duplicate-looking readings ({len(pending_r14)})")
        for fname, result, fstate in pending_r14:
            with st.container(border=True):
                st.write(f"**{fname}**")
                st.caption(f"{result.pause_code}: {result.pause_message}")
                c1, c2 = st.columns(2)
                if c1.button("Use this date anyway", key=f"r14_confirm_{fname}"):
                    fstate["r14_confirmed"] = True
                    result.issues.append(dict(rule_code=result.pause_code,
                                              action=f"Confirmed anyway by {user}: {result.pause_message}"))
                    st.rerun()
                if c2.button("Cancel upload", key=f"r14_cancel_{fname}"):
                    fstate["control"] = None
                    fstate["reject"] = "Cancelled by uploader (R14)"
                    st.rerun()

    if rejected:
        st.subheader(f"Rejected ({len(rejected)})")
        st.dataframe(pd.DataFrame(rejected, columns=["File", "Reason"]), use_container_width=True, hide_index=True)

    if ready:
        st.subheader(f"Ready ({len(ready)})")
        summary_rows, all_issues, all_unmatched = [], [], []
        for fname, fstate in ready:
            r = fstate["parsed"]
            blank_bands = sum(1 for i in r.issues if i["rule_code"] == "R34")
            summary_rows.append(dict(
                File=fname, Region=fstate["region"], Date=str(fstate["date"]),
                Feeders=r.feeders_read, **{"Rows saved": r.rows_saved, "Rows skipped": r.rows_skipped,
                "Blank bands": blank_bands, "Unmatched feeders": len(r.unmatched_rows), "Warnings": len(r.issues)},
            ))
            for i in r.issues:
                all_issues.append(dict(file=fname, **i))
            for un in r.unmatched_rows:
                all_unmatched.append(dict(file=fname, **un))
        st.dataframe(one_indexed(pd.DataFrame(summary_rows)), use_container_width=True)

        if all_issues:
            with st.expander(f"Issues ({len(all_issues)})"):
                issues_df = pd.DataFrame(all_issues)
                st.dataframe(one_indexed(issues_df), use_container_width=True)
                st.download_button("Download issues (CSV)", issues_df.to_csv(index=False), "workbook_issues.csv", "text/csv")

        if all_unmatched:
            st.subheader(f"Unmatched feeders ({len(all_unmatched)}) -- link or create before saving")
            with engine.connect() as con:
                from sqlalchemy import text as _text
                known = con.execute(_text("SELECT DISTINCT station, canonical_name FROM feeder")).fetchall()
            by_station: dict[str, list[str]] = {}
            for st_, name in known:
                by_station.setdefault(st_, []).append(name)

            for idx, un in enumerate(all_unmatched):
                with st.container(border=True):
                    st.write(f"**{un['station']} / {un['feeder_name']}** ({un['file']})")
                    candidates = by_station.get(un["station"], [])
                    suggestions = fuzz_process.extract(un["feeder_name"], candidates, limit=3) if candidates else []
                    if suggestions:
                        st.caption("Closest known feeders at this station: " +
                                  ", ".join(f"{s[0]} ({s[1]:.0f}%)" for s in suggestions))
                    action = st.radio("Action", ["Leave for now", "Link to an existing feeder", "Create as a new feeder"],
                                      key=f"unmatch_action_{idx}", horizontal=True)
                    if action == "Link to an existing feeder" and candidates:
                        pick = st.selectbox("Existing feeder", candidates, key=f"unmatch_pick_{idx}")
                        if st.button("Link", key=f"unmatch_link_{idx}"):
                            from sqlalchemy import text as _text2
                            with engine.begin() as con2:
                                row = con2.execute(_text2("SELECT feeder_id, station_norm FROM feeder WHERE station=:s AND canonical_name=:n"),
                                                   dict(s=un["station"], n=pick)).fetchone()
                                if row:
                                    from reliability_report.data import normalize_feeder as _nf
                                    con2.execute(_text2(
                                        "INSERT INTO feeder_alias (feeder_id, source, station_norm, name_raw, name_norm, created_by) "
                                        "VALUES (:fid, 'daily_workbook', :sn, :raw, :norm, :by) ON CONFLICT DO NOTHING"
                                    ), dict(fid=row[0], sn=row[1], raw=un["feeder_name"], norm=_nf(un["feeder_name"]), by=user))
                            st.success(f"Linked '{un['feeder_name']}' to feeder #{row[0] if row else '?'} -- re-run the upload to pick it up.")
                    elif action == "Create as a new feeder":
                        if st.button("Create", key=f"unmatch_create_{idx}"):
                            from sqlalchemy import text as _text3
                            from reliability_report.data import normalize_station as _ns, normalize_feeder as _nf
                            with engine.begin() as con3:
                                fid = con3.execute(_text3(
                                    "INSERT INTO feeder (region, disco, area_control, station, station_norm, transformer, "
                                    "rating, feeder_band, canonical_name, created_by, updated_by) "
                                    "VALUES (:region, :disco, :area, :station, :sn, :tr, :rating, :band, :name, :by, :by) "
                                    "RETURNING feeder_id"
                                ), dict(region=un["region"], disco=un.get("disco"), area=un.get("area_control"),
                                       station=un["station"], sn=_ns(un["station"]), tr=un.get("transformer"),
                                       rating=un.get("rating"), band=un.get("feeder_band"), name=un["feeder_name"], by=user)).scalar_one()
                                con3.execute(_text3(
                                    "INSERT INTO feeder_alias (feeder_id, source, station_norm, name_raw, name_norm, created_by) "
                                    "VALUES (:fid, 'daily_workbook', :sn, :raw, :norm, :by)"
                                ), dict(fid=fid, sn=_ns(un["station"]), raw=un["feeder_name"], norm=_nf(un["feeder_name"]), by=user))
                            st.success(f"Created feeder #{fid} -- re-run the upload to pick it up.")

        if st.button("Save all ready files", type="primary"):
            saved = []
            for fname, fstate in ready:
                rid = ff_store.save_upload(engine, fstate["parsed"], fname, user,
                                           region_source=fstate["region_source"], date_source=fstate["date_source"])
                saved.append((fname, rid))
                log_activity("upload_feeder_forecast",
                            f"'{fname}' -- {fstate['region']} {fstate['date']}, upload #{rid}, "
                            f"{fstate['parsed'].rows_saved} row(s)")
                st.session_state["ffc_state"].pop(fname, None)
            st.success(f"Saved {len(saved)} file(s): " + ", ".join(f"{f} (#{i})" for f, i in saved))
            st.rerun()
