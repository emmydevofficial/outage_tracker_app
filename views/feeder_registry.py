"""
### FILE: views/feeder_registry.py
Admin page for the feeder identity registry (feeder_forecast feature):
browse feeders and their aliases, edit band/disco/status, rename
(adds a new alias, never rewrites history), retire/replace, and resolve
the unmatched-name queue left behind by workbook uploads. Every change
is logged.
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rapidfuzz import process as fuzz_process
from sqlalchemy import text

from reliability_report.data import normalize_feeder, normalize_station
from utils.auth import require_super_admin
from utils.activity_log import log_activity
from utils.branding import page_header, one_indexed
from utils.db import get_engine

require_super_admin()
page_header("Feeder Registry", "33kV Feeder Network · Forecast & Daily Energy Data")
st.caption(
    "The feeder identity list used by the daily workbook upload and (later) the forecast-based reports. "
    "Names change over time; a rename adds a new alias here rather than rewriting any saved history."
)

engine = get_engine()
user = st.session_state.get("username")

tab_browse, tab_unmatched = st.tabs(["Browse & edit", "Unmatched names"])

with tab_browse:
    with engine.connect() as con:
        feeders = pd.read_sql_query(text(
            "SELECT feeder_id, region, disco, area_control, station, transformer, rating, "
            "feeder_band, canonical_name, status, replaced_by_feeder_id FROM feeder ORDER BY region, station, canonical_name"
        ), con)

    c1, c2, c3 = st.columns(3)
    region_f = c1.selectbox("Region", ["All"] + sorted(feeders["region"].dropna().unique()))
    status_f = c2.selectbox("Status", ["All", "active", "retired"])
    search = c3.text_input("Search station/feeder")

    view = feeders.copy()
    if region_f != "All":
        view = view[view["region"] == region_f]
    if status_f != "All":
        view = view[view["status"] == status_f]
    if search:
        s = search.strip().lower()
        view = view[view["station"].str.lower().str.contains(s) | view["canonical_name"].str.lower().str.contains(s)]

    st.write(f"{len(view)} feeder(s)")
    st.dataframe(one_indexed(view), use_container_width=True, height=300)

    st.subheader("Edit a feeder")
    pick_id = st.selectbox("Feeder", view["feeder_id"], format_func=lambda i: (
        f"#{i} · {view.set_index('feeder_id').loc[i,'station']} / {view.set_index('feeder_id').loc[i,'canonical_name']}"
    ) if i in view["feeder_id"].values else str(i))

    if pick_id:
        row = feeders[feeders["feeder_id"] == pick_id].iloc[0]
        with engine.connect() as con:
            aliases = pd.read_sql_query(text(
                "SELECT source, name_raw, name_norm, valid_from, valid_to, created_by, created_at "
                "FROM feeder_alias WHERE feeder_id=:f ORDER BY created_at"
            ), con, params={"f": int(pick_id)})
        st.dataframe(one_indexed(aliases), use_container_width=True)

        with st.form(f"edit_feeder_{pick_id}"):
            e1, e2, e3 = st.columns(3)
            new_band = e1.text_input("Band", value=row["feeder_band"] or "")
            new_disco = e2.text_input("DisCo", value=row["disco"] or "")
            new_status = e3.selectbox("Status", ["active", "retired"], index=0 if row["status"] == "active" else 1)
            rename_to = st.text_input("Rename to (adds a new alias, leaves history as-is)", value="")
            if st.form_submit_button("Save changes"):
                with engine.begin() as con:
                    con.execute(text(
                        "UPDATE feeder SET feeder_band=:b, disco=:d, status=:s, updated_by=:u, updated_at=now() WHERE feeder_id=:f"
                    ), dict(b=new_band or None, d=new_disco or None, s=new_status, u=user, f=int(pick_id)))
                    if rename_to.strip():
                        con.execute(text(
                            "INSERT INTO feeder_alias (feeder_id, source, station_norm, name_raw, name_norm, created_by) "
                            "VALUES (:f, 'daily_workbook', :sn, :raw, :norm, :by) ON CONFLICT DO NOTHING"
                        ), dict(f=int(pick_id), sn=normalize_station(row["station"]), raw=rename_to.strip(),
                               norm=normalize_feeder(rename_to.strip()), by=user))
                        con.execute(text("UPDATE feeder SET canonical_name=:n WHERE feeder_id=:f"),
                                   dict(n=rename_to.strip(), f=int(pick_id)))
                log_activity("feeder_registry_edit", f"Feeder #{pick_id} ({row['station']}/{row['canonical_name']}): "
                                                      f"band={new_band}, disco={new_disco}, status={new_status}"
                                                      + (f", renamed to '{rename_to.strip()}'" if rename_to.strip() else ""))
                st.success("Saved.")
                st.rerun()

        st.caption("Retire this feeder and point it at a replacement (a split/merge) -- does not touch saved history.")
        r1, r2 = st.columns(2)
        replacement_id = r1.number_input("Replacement feeder_id (optional)", min_value=0, step=1, value=0)
        if r2.button("Retire this feeder"):
            with engine.begin() as con:
                con.execute(text(
                    "UPDATE feeder SET status='retired', replaced_by_feeder_id=:r, updated_by=:u, updated_at=now() WHERE feeder_id=:f"
                ), dict(r=int(replacement_id) or None, u=user, f=int(pick_id)))
            log_activity("feeder_registry_retire", f"Feeder #{pick_id} retired" +
                        (f", replaced by #{replacement_id}" if replacement_id else ""))
            st.success("Retired.")
            st.rerun()

with tab_unmatched:
    with engine.connect() as con:
        staged = pd.read_sql_query(text(
            "SELECT staging_id, upload_id, station_raw, feeder_raw, region, created_at "
            "FROM feeder_upload_staging WHERE status='pending' ORDER BY created_at"
        ), con)
        known = con.execute(text("SELECT DISTINCT station, canonical_name FROM feeder")).fetchall()
    by_station: dict[str, list[str]] = {}
    for st_, name in known:
        by_station.setdefault(st_, []).append(name)

    if staged.empty:
        st.write("No unmatched names waiting for review.")
    else:
        st.write(f"{len(staged)} unmatched name(s) from workbook uploads.")
        for _, s in staged.iterrows():
            with st.container(border=True):
                st.write(f"**{s['station_raw']} / {s['feeder_raw']}** ({s['region']}, staged {s['created_at']})")
                candidates = by_station.get(s["station_raw"], [])
                suggestions = fuzz_process.extract(s["feeder_raw"], candidates, limit=3) if candidates else []
                if suggestions:
                    st.caption("Closest known feeders: " + ", ".join(f"{m[0]} ({m[1]:.0f}%)" for m in suggestions))
                c1, c2 = st.columns(2)
                if candidates and c1.button("Link to " + (suggestions[0][0] if suggestions else candidates[0]), key=f"link_{s['staging_id']}"):
                    target = suggestions[0][0] if suggestions else candidates[0]
                    with engine.begin() as con:
                        row = con.execute(text("SELECT feeder_id, station_norm FROM feeder WHERE station=:st AND canonical_name=:n"),
                                         dict(st=s["station_raw"], n=target)).fetchone()
                        if row:
                            con.execute(text(
                                "INSERT INTO feeder_alias (feeder_id, source, station_norm, name_raw, name_norm, created_by) "
                                "VALUES (:fid,'daily_workbook',:sn,:raw,:norm,:by) ON CONFLICT DO NOTHING"
                            ), dict(fid=row[0], sn=row[1], raw=s["feeder_raw"], norm=normalize_feeder(s["feeder_raw"]), by=user))
                            con.execute(text(
                                "UPDATE feeder_upload_staging SET status='resolved', resolved_feeder_id=:fid, "
                                "resolved_by=:by, resolved_at=now() WHERE staging_id=:sid"
                            ), dict(fid=row[0], by=user, sid=int(s["staging_id"])))
                    log_activity("feeder_registry_resolve", f"Staged '{s['station_raw']}/{s['feeder_raw']}' linked to feeder #{row[0] if row else '?'}")
                    st.rerun()
                if c2.button("Discard", key=f"discard_{s['staging_id']}"):
                    with engine.begin() as con:
                        con.execute(text("UPDATE feeder_upload_staging SET status='discarded', resolved_by=:by, resolved_at=now() WHERE staging_id=:sid"),
                                   dict(by=user, sid=int(s["staging_id"])))
                    st.rerun()
