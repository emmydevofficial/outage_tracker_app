"""
### FILE: views/reliability_kpi_report_v2.py
"Reliability KPI (Forecast Loss)" -- same date range, Region/Disco/Area/
Station filters, month-clipping and SLA exceedance logic as
views/reliability_kpi_report.py (not edited, stays the default page).
Only the load source for TCN's excess-hours cost changes: instead of a
flat last_load x duration assumption, the excess tail of an outage is
sliced hour-by-hour against the daily workbook's forecast
(feeder_hourly_forecast, via utils/forecast_loss.py).

Nothing is filled or priced silently: missing forecast coverage is shown
up front, and a last_load-filled total only appears after the user
explicitly confirms which gap periods to fill -- see spec sections 8-9.
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reliability_report.data import (
    read_outages, read_sla, read_tariff_rates, read_default_tariff,
    sla_lookup, tariff_lookup, get_rate,
)
from reliability_report.metrics import sla_compliance, exceedance as old_exceedance
from feeder_forecast.parser import load_alias_maps, resolve_outage_feeder_cached
from utils.forecast_loss import (
    month_clip_bounds, excess_forecast_loss, cost_ngn, split_interval, load_method, gap_periods,
)
from utils.auth import filter_to_user_region
from utils.activity_log import log_activity
from utils.branding import page_header, one_indexed, kpi_card, kpi_grid
from utils.db import get_engine
from utils.regions import REGIONS

page_header("Reliability KPI (Forecast Loss)", "33kV Feeder Network · SLA Exceedance Priced from Daily Workbook Forecast")
st.caption(
    "Same filters, month-clipping and SLA exceedance logic as the Reliability KPI Report -- TCN's excess-hours "
    "cost is priced from the uploaded daily feeder workbook forecast instead of a flat last-load assumption. "
    "The original Reliability KPI Report is unchanged and stays the primary page."
)

engine = get_engine()
user = st.session_state.get("username")

today = dt.date.today()
start_default = today - dt.timedelta(days=30)
date_range = st.date_input("Select date range", value=[start_default, today], key="kpi2_dates")
if not isinstance(date_range, (list, tuple)) or len(date_range) != 2:
    st.info("Pick a start and end date.")
    st.stop()
start_date, end_date = date_range
days_span = (end_date - start_date).days + 1

out_df = read_outages(start_date, end_date)
out_df = filter_to_user_region(out_df)
if out_df.empty:
    st.warning("No outage records for this range")
    st.stop()

col1, col2, col3, col4 = st.columns(4)
region_sel = col1.selectbox("Region", options=["All"] + sorted(out_df["region"].dropna().unique()), key="kpi2_region")
if region_sel != "All":
    out_df = out_df[out_df["region"] == region_sel]
disco_sel = col2.selectbox("Disco", options=["All"] + sorted(out_df["disco"].dropna().unique()), key="kpi2_disco")
if disco_sel != "All":
    out_df = out_df[out_df["disco"] == disco_sel]
area_sel = col3.selectbox("Area", options=["All"] + sorted(out_df["area"].dropna().unique()), key="kpi2_area")
if area_sel != "All":
    out_df = out_df[out_df["area"] == area_sel]
station_sel = col4.selectbox("Station", options=["All"] + sorted(out_df["station"].dropna().unique()), key="kpi2_station")
if station_sel != "All":
    out_df = out_df[out_df["station"] == station_sel]

# Month clipping -- same rule as the existing Reliability KPI Report:
# clip to the FULL calendar month(s) the selected range falls in, not
# just the exact selected days (so "viewing March only" counts all of
# March for any outage overlapping the fetched range).
month_start, month_end = month_clip_bounds(start_date, end_date)
out_df["clipped_start"] = out_df["start_ts"].clip(lower=month_start, upper=month_end)
out_df["clipped_end"] = out_df["end_ts"].clip(lower=month_start, upper=month_end)
out_df["duration_hr"] = ((out_df["clipped_end"] - out_df["clipped_start"]).dt.total_seconds() / 3600.0).clip(lower=0)
out_df.loc[out_df["is_open"], "duration_hr"] = 0.0

df_tcn = out_df[(out_df["party_responsible"] == "TCN") & (~out_df["is_open"])].copy()
if df_tcn.empty:
    st.info("No TCN-attributed outages for this selection.")
    st.stop()

sla_map = sla_lookup(read_sla())
tariff_df = read_tariff_rates()
tariff_map = tariff_lookup(tariff_df)
default_rate = read_default_tariff()

comp = sla_compliance(df_tcn, sla_map, days_span)
over = comp[comp["available_hrs"] < 0].copy()
if over.empty:
    st.info("No station/feeder exceeded TCN's available outage duration for this period.")
    st.stop()
over["excess_hrs"] = (-over["available_hrs"]).round(2)

# --- resolve each over-allocation feeder to the feeder_forecast registry ---
alias_maps = load_alias_maps(engine)
over["feeder_id"] = over.apply(
    lambda r: resolve_outage_feeder_cached(alias_maps, r["station"], r["feeder"]), axis=1)
allocation_hrs_by_feeder = {(r["station"], r["feeder"]): r["allocation_hrs"] for _, r in over.iterrows()}
feeder_id_by_feeder = {
    (r["station"], r["feeder"]): (None if pd.isna(r["feeder_id"]) else int(r["feeder_id"]))
    for _, r in over.iterrows()
}
unmatched_feeders = over[over["feeder_id"].isna()][["station", "feeder"]].drop_duplicates()

# --- load just the forecast rows these feeders need, across the window ---
feeder_ids = sorted({v for v in feeder_id_by_feeder.values() if v is not None})
slot_lo = dt.datetime.combine(start_date - dt.timedelta(days=1), dt.time(0, 0))
slot_hi = dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time(0, 0))
forecast_map: dict[tuple[int, dt.datetime], float | None] = {}
if feeder_ids:
    with engine.connect() as con:
        rows = con.execute(text(
            "SELECT feeder_id, slot_start, forecast_mw FROM feeder_hourly_forecast "
            "WHERE feeder_id = ANY(:fids) AND slot_start >= :lo AND slot_start < :hi"
        ), dict(fids=feeder_ids, lo=slot_lo, hi=slot_hi)).fetchall()
    for fid, slot_start, forecast_mw in rows:
        forecast_map[(fid, slot_start)] = None if forecast_mw is None else float(forecast_mw)

# --- preliminary (always-unpriced) pass to find every gap, for the review table and banner ---
prelim = excess_forecast_loss(df_tcn, allocation_hrs_by_feeder, feeder_id_by_feeder, forecast_map, gap_policy="unpriced")
all_gap_rows: list[dict] = []
for rec in prelim:
    outage_row = df_tcn.loc[df_tcn["id"] == rec["outage_id"]].iloc[0]
    row_dict = dict(id=rec["outage_id"], station=rec["station"], region=outage_row["region"],
                     feeder_33kv=rec["feeder"], last_load=outage_row["last_load"])
    all_gap_rows.extend(gap_periods(row_dict, rec["slices"]))

missing_pairs = sorted({(g["region"], g["gap_start"].date()) for g in all_gap_rows
                         if g["reason"] == "no workbook for that date"})

# ======================================================================
# Rendering, in the order the spec asks for: coverage, then the gap
# review/approval workflow, then the priced comparison tables.
# ======================================================================

st.subheader("Data coverage")
grid_start = start_date - dt.timedelta(days=1)
grid_dates = [grid_start + dt.timedelta(days=i) for i in range((end_date - grid_start).days + 1)]
with engine.connect() as con:
    uploads_df = pd.read_sql_query(text(
        "SELECT resolved_region, resolved_date, uploaded_by, uploaded_at FROM daily_workbook_upload "
        "WHERE status IN ('saved', 'saved_with_warnings') AND resolved_date BETWEEN :a AND :b"
    ), con, params={"a": grid_start, "b": end_date})
coverage = {(r["resolved_region"], r["resolved_date"]) for _, r in uploads_df.iterrows()}
grid_rows = []
for region in REGIONS:
    row = {"Region": region}
    for d in grid_dates:
        row[d.strftime("%d %b")] = "✓" if (region, d) in coverage else "—"
    grid_rows.append(row)
st.dataframe(pd.DataFrame(grid_rows).set_index("Region"), use_container_width=True)
st.caption(
    "✓ = that region's daily workbook for that date has been uploaded. The day before the range start is "
    "included since its 24:00 column supplies the 00:00-01:00 forecast for the first day shown."
)

if missing_pairs:
    st.warning("Figures are provisional: forecast data is missing for " +
               ", ".join(f"{r} {d}" for r, d in missing_pairs))

if not unmatched_feeders.empty:
    with st.expander(f"⚠️ {len(unmatched_feeders)} feeder(s) not yet matched to the forecast registry"):
        st.dataframe(one_indexed(unmatched_feeders), use_container_width=True)
        st.caption("These show as unpriced (\"feeder not matched\") below until linked or created on the "
                   "Feeder Registry page's Unmatched names tab.")

state_key = f"kpi2_gap::{start_date}:{end_date}:{region_sel}:{disco_sel}:{area_sel}:{station_sel}"

if all_gap_rows:
    st.subheader(f"Missing forecast ({len(all_gap_rows)} period(s))")
    gap_df = pd.DataFrame([dict(
        region=g["region"], station=g["station"], feeder=g["feeder"], outage_id=g["outage_id"],
        gap_from=g["gap_start"], gap_to=g["gap_end"], minutes=g["minutes"], reason=g["reason"],
        last_load_available="Yes" if g["last_load"] is not None and not pd.isna(g["last_load"]) else "No",
        last_load_mw=g["last_load"],
    ) for g in all_gap_rows])
    st.dataframe(one_indexed(gap_df), use_container_width=True)

    st.write("These feeders have no forecast for the periods above. Do you want to use the last recorded "
             "load (last_load) for these periods?")
    choice = st.radio("Gap handling", ["No, leave them unpriced", "Yes, use last_load for all listed periods",
                                        "Let me choose"], index=0, key=f"{state_key}::choice")

    ticked = set()
    if choice == "Let me choose":
        for i, g in enumerate(all_gap_rows):
            label = f"{g['station']} / {g['feeder']}: {g['gap_start']} to {g['gap_end']} ({g['minutes']:.0f} min, {g['reason']})"
            if st.checkbox(label, key=f"{state_key}::tick::{i}"):
                ticked.add(i)

    if st.button("Confirm and recalculate", key=f"{state_key}::confirm", type="primary"):
        if choice == "No, leave them unpriced":
            approved_idx = set()
        elif choice == "Yes, use last_load for all listed periods":
            approved_idx = set(range(len(all_gap_rows)))
        else:
            approved_idx = ticked

        approved_slot_keys = set()
        approved_periods = []
        for i in approved_idx:
            g = all_gap_rows[i]
            feeder_id = feeder_id_by_feeder.get((g["station"], g["feeder"]))
            for slot_start, _ in split_interval(g["gap_start"], g["gap_end"]):
                approved_slot_keys.add((feeder_id, slot_start))
            approved_periods.append(g)

        approval_id = None
        approved_at = dt.datetime.now()
        if approved_idx:
            gap_list_json = [
                {k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in g.items()}
                for g in approved_periods
            ]
            with engine.begin() as con:
                approval_id = con.execute(text(
                    "INSERT INTO forecast_gap_approval (approved_by, date_from, date_to, filters, gap_list) "
                    "VALUES (:by, :a, :b, :f, :g) RETURNING approval_id"
                ), dict(by=user, a=start_date, b=end_date,
                       f=json.dumps(dict(region=region_sel, disco=disco_sel, area=area_sel, station=station_sel)),
                       g=json.dumps(gap_list_json))).scalar_one()
            log_activity("forecast_kpi_gap_approval",
                        f"{len(approved_idx)} of {len(all_gap_rows)} gap period(s) approved for last_load, "
                        f"{start_date} to {end_date}, approval #{approval_id}")

        st.session_state[state_key] = dict(
            gap_policy="last_load" if approved_idx else "unpriced",
            approved_slot_keys=approved_slot_keys, approval_id=approval_id,
            approved_at=approved_at, approved_by=user, approved_periods=approved_periods,
        )
        st.rerun()
else:
    st.session_state.setdefault(state_key, dict(
        gap_policy="unpriced", approved_slot_keys=set(), approval_id=None,
        approved_at=None, approved_by=None, approved_periods=[],
    ))

gap_state = st.session_state.get(state_key, dict(
    gap_policy="unpriced", approved_slot_keys=set(), approval_id=None,
    approved_at=None, approved_by=None, approved_periods=[],
))
gap_policy = gap_state["gap_policy"]

if gap_state.get("approval_id"):
    st.success(f"last_load was used for {len(gap_state['approved_periods'])} period(s) "
               f"({sum(p['minutes'] for p in gap_state['approved_periods']) / 60:.1f} hours) "
               f"approved by {gap_state['approved_by']} at {gap_state['approved_at']:%Y-%m-%d %H:%M}.")
    with st.expander("Approved periods"):
        st.dataframe(one_indexed(pd.DataFrame([
            dict(station=p["station"], feeder=p["feeder"], gap_from=p["gap_start"], gap_to=p["gap_end"])
            for p in gap_state["approved_periods"]
        ])), use_container_width=True)

# --- final priced pass, honoring the confirmed gap policy ---
final = excess_forecast_loss(df_tcn, allocation_hrs_by_feeder, feeder_id_by_feeder, forecast_map,
                             gap_policy=gap_policy, approved_slot_keys=gap_state["approved_slot_keys"])

agg: dict[tuple, dict] = {}
for rec in final:
    key = (rec["station"], rec["feeder"])
    a = agg.setdefault(key, dict(new_excess_mwh=0.0, hrs_forecast=0.0, hrs_last_load=0.0, hrs_unpriced=0.0, slices=[]))
    a["new_excess_mwh"] += rec["excess_forecast_mwh"]
    a["slices"].extend(rec["slices"])
    for s in rec["slices"]:
        hrs = s.minutes / 60.0
        if s.source == "forecast":
            a["hrs_forecast"] += hrs
        elif s.source == "last_load (approved)":
            a["hrs_last_load"] += hrs
        else:
            a["hrs_unpriced"] += hrs

old = old_exceedance(df_tcn, sla_map, tariff_map, default_rate, days_span)

rows = []
for _, r in over.iterrows():
    key = (r["station"], r["feeder"])
    a = agg.get(key, dict(new_excess_mwh=0.0, hrs_forecast=0.0, hrs_last_load=0.0, hrs_unpriced=0.0, slices=[]))
    old_row = old[(old["station"] == r["station"]) & (old["feeder"] == r["feeder"])]
    old_mwh = float(old_row["excess_load_loss_mwh"].iloc[0]) if not old_row.empty else 0.0
    old_cost = float(old_row["estimated_cost_ngn"].iloc[0]) if not old_row.empty else 0.0
    band, disco = r["band"], r.get("disco")
    rate = get_rate(disco, band, tariff_map, default_rate)
    rate_source = "disco_band" if (disco, band) in tariff_map else "default_rate"
    new_cost = cost_ngn(a["new_excess_mwh"], rate)
    rows.append(dict(
        station=r["station"], feeder=r["feeder"], tcn_hrs=round(r["tcn_hrs"], 2), excess_hrs=round(r["excess_hrs"], 2),
        old_excess_mwh=round(old_mwh, 2), new_excess_mwh=round(a["new_excess_mwh"], 4),
        old_cost_ngn=round(old_cost, 2), new_cost_ngn=new_cost, difference_ngn=round(new_cost - old_cost, 2),
        load_method=load_method(a["slices"]) if a["slices"] else "unpriced",
        hrs_forecast=round(a["hrs_forecast"], 2), hrs_last_load=round(a["hrs_last_load"], 2),
        hrs_unpriced=round(a["hrs_unpriced"], 2),
        band_used=band, band_source="assumed" if r["band_assumed"] else "sla",
        rate_used=rate, rate_source=rate_source,
    ))
comparison_df = pd.DataFrame(rows)

st.subheader("💰 Excess Cost — Last-Load Method vs Forecast Method")
kpi_grid([
    kpi_card("Feeders Over Allocation", f"{len(comparison_df)}", "", "alert", "#c81e28"),
    kpi_card("Old Excess (last_load)", f"{comparison_df['old_excess_mwh'].sum():.2f}", "MWh", "bolt", "#956400"),
    kpi_card("New Excess (forecast)", f"{comparison_df['new_excess_mwh'].sum():.2f}", "MWh", "bolt", "#1e3a7a"),
])
kpi_grid([
    kpi_card("Old Cost", f"₦{comparison_df['old_cost_ngn'].sum():,.2f}", "", "chart", "#956400"),
    kpi_card("New Cost", f"₦{comparison_df['new_cost_ngn'].sum():,.2f}", "", "chart", "#1e3a7a"),
    kpi_card("Difference", f"₦{comparison_df['difference_ngn'].sum():,.2f}", "", "chart", "#c81e28"),
])
kpi_grid([
    kpi_card("Hours: Forecast", f"{comparison_df['hrs_forecast'].sum():.1f}", "hrs", "clock", "#1e3a7a"),
    kpi_card("Hours: Approved Last-Load", f"{comparison_df['hrs_last_load'].sum():.1f}", "hrs", "clock", "#956400"),
    kpi_card("Hours: Unpriced", f"{comparison_df['hrs_unpriced'].sum():.1f}", "hrs", "clock", "#c81e28"),
])
if comparison_df["hrs_unpriced"].sum() > 0:
    st.caption(f"⚠️ Unpriced outage time: {comparison_df['hrs_unpriced'].sum():.1f} hours on "
               f"{(comparison_df['hrs_unpriced'] > 0).sum()} feeder(s) -- a smaller total here is NOT a complete one.")


def _highlight(row):
    if row["hrs_unpriced"] > 0 or row["hrs_last_load"] > 0:
        return ["background-color: rgba(200,30,40,0.08)"] * len(row)
    return [""] * len(row)


st.dataframe(
    comparison_df.style.apply(_highlight, axis=1),
    use_container_width=True,
    column_config={
        "old_excess_mwh": st.column_config.NumberColumn("Old Excess (MWh)", format="%.2f"),
        "new_excess_mwh": st.column_config.NumberColumn("New Excess (MWh)", format="%.4f"),
        "old_cost_ngn": st.column_config.NumberColumn("Old Cost (₦)", format="₦ %,.2f"),
        "new_cost_ngn": st.column_config.NumberColumn("New Cost (₦)", format="₦ %,.2f"),
        "difference_ngn": st.column_config.NumberColumn("Difference (₦)", format="₦ %,.2f"),
        "rate_used": st.column_config.NumberColumn("Rate Used (₦/kWh)", format="₦ %.2f"),
    },
)
st.caption("Rows with any approved-last-load or unpriced time are highlighted, the same way an assumed band "
           "is highlighted elsewhere in this app.")

st.subheader("Outage slice detail")
for rec in final:
    with st.expander(f"Outage #{rec['outage_id']} — {rec['station']} / {rec['feeder']} — "
                      f"excess {rec['excess_hrs']:.2f} hrs, {rec['excess_forecast_mwh']:.4f} MWh"):
        slice_rows = [dict(slot_date=s.slot_start.date(), slot_time=s.slot_start.time(), minutes=s.minutes,
                           source=s.source, mw_used=s.forecast_mw, mwh=round(s.mwh, 4),
                           source_workbook_date=s.slot_start.date()) for s in rec["slices"]]
        st.dataframe(one_indexed(pd.DataFrame(slice_rows)), use_container_width=True)

st.subheader("Export")


def _export_header() -> str:
    lines = [
        f"# Date range: {start_date} to {end_date}",
        f"# Filters: region={region_sel}, disco={disco_sel}, area={area_sel}, station={station_sel}",
        f"# Generated by {user} at {dt.datetime.now():%Y-%m-%d %H:%M}",
        f"# Coverage: " + ("complete" if not missing_pairs else "; ".join(f"{r} {d}" for r, d in missing_pairs)),
        f"# Gap policy: {gap_policy}",
    ]
    if gap_state.get("approval_id"):
        lines.append(f"# last_load approval #{gap_state['approval_id']} by {gap_state['approved_by']} "
                     f"at {gap_state['approved_at']:%Y-%m-%d %H:%M}, {len(gap_state['approved_periods'])} period(s)")
    return "\n".join(lines) + "\n"


export_header = _export_header()
st.download_button("Download comparison (CSV)", export_header + comparison_df.to_csv(index=False),
                   "forecast_loss_comparison.csv", "text/csv")

slice_export_rows = []
for rec in final:
    for s in rec["slices"]:
        slice_export_rows.append(dict(station=rec["station"], feeder=rec["feeder"], outage_id=rec["outage_id"],
                                      slot_start=s.slot_start, minutes=s.minutes, source=s.source,
                                      mw_used=s.forecast_mw, mwh=round(s.mwh, 4)))
st.download_button("Download slice detail (CSV)",
                   export_header + pd.DataFrame(slice_export_rows).to_csv(index=False),
                   "forecast_loss_slices.csv", "text/csv")
