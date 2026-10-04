"""
### FILE: views/sla_outage_reports.py
Super Admin only: weekly/monthly SLA outage-hour exceedance reports, one
Word document per region plus a management summary, covering how much of
TCN's 30% outage-hour allocation each feeder has used and what it's
costing -- so Operations can point management at specific
feeders/stations/equipment to cut outage losses.

Entirely new files under reliability_report/ + this page + the rel_*
tables -- nothing in views/reliability_kpi_report.py or
views/generate_reports.py is touched; this page only *reads*
outages/tcn_sla_compliance/tariff_rates/tariff_settings.
"""
import calendar
import datetime as dt
import io
import os
import sys
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reliability_report import data as D
from reliability_report import metrics as M
from reliability_report import narrative as N
from reliability_report import docx_builder as B
from reliability_report import store
from reliability_report.periods import week_periods, month_period, month_to_date

from utils.auth import require_super_admin
from utils.db import get_engine
from utils.activity_log import log_activity
from utils.branding import page_header
from utils.regions import REGIONS

require_super_admin()
page_header("SLA Outage Reports", "33kV Feeder Network · Weekly & Monthly SLA Exceedance")
st.caption(
    "Weekly or monthly SLA outage-hour exceedance reports (Word), one per region plus a management summary. "
    "TCN's allocation is 30% of each feeder's DISCO-band allowed outage hours; a feeder missing a band uses "
    "Band A. Weeks run Sunday to Saturday, cut at month boundaries."
)

engine = get_engine()
user = st.session_state.get("username")
ai_provider = os.getenv("AI_PROVIDER")
ai_api_key = os.getenv("AI_API_KEY")
ai_model = os.getenv("AI_MODEL")

def _region_cost(period_facts: dict, region: str) -> float:
    """A period's per-region cost, whichever shape its facts happen to be
    in: a real run's facts (region=None) has exceedance_by_region (a list
    of per-region dicts); the seeded legacy Week 1/2 snapshots only have a
    flat cost_by_region dict (region -> cost)."""
    for r in period_facts.get("exceedance_by_region", []):
        if r.get("region") == region:
            return r.get("estimated_cost_ngn", 0)
    cbr = period_facts.get("cost_by_region", {})
    return cbr.get(region, 0)


def _comparative_tables(engine, period_type_key: str, period, current_facts: dict, all_regions: list[str]):
    """Issued prior periods of the same type up to and including this one
    (weeks of the month so far, or months so far), for the "Comparative
    Performance" section. current_facts stands in for the period being
    generated right now (not yet saved/issued)."""
    if period_type_key == "week":
        prior = [w for w in week_periods(period.start.year, period.start.month) if w.end < period.end]
    else:
        prev_month_end = period.start - dt.timedelta(days=1)
        prior = [month_period(prev_month_end.year, prev_month_end.month)]
    issued = []
    for p in prior:
        run = store.get_issued_run(engine, period_type_key, p.start, p.end, "ALL")
        if run:
            issued.append(dict(period_label=p.label, facts=run["facts"]))
    issued.append(dict(period_label=period.label, facts=current_facts))
    if len(issued) < 2:
        return dict(issued_periods=[], comparative_rows=[], comparative_region_rows=[])

    metric_rows = []
    for key, label, is_cost in [("outages", "Total Outages", False), ("hours", "Total Outage Hours", False),
                                ("load_loss_mwh", "Total Load Loss (MWh)", False),
                                ("feeders_over_allocation", "Feeders Over Allocation", False),
                                ("excess_hrs", "Excess Hours", False), ("excess_load_loss_mwh", "Excess Load Loss (MWh)", False),
                                ("estimated_cost_ngn", "Estimated Cost", True)]:
        row = [label]
        for p in issued:
            v = p["facts"].get(key, 0)
            row.append(f"₦{v:,.2f}" if is_cost else f"{v:,}")
        metric_rows.append(row)

    region_rows = []
    for r in all_regions:
        row = [r] + [f"₦{_region_cost(p['facts'], r):,.2f}" for p in issued]
        region_rows.append(row)

    return dict(issued_periods=[dict(period_label=p["period_label"]) for p in issued],
                comparative_rows=metric_rows, comparative_region_rows=region_rows)


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


tab_generate, tab_history = st.tabs(["Generate", "History"])

with tab_generate:
    c1, c2 = st.columns(2)
    today = dt.date.today()
    year = c1.number_input("Year", 2024, 2100, today.year)
    month = c2.selectbox("Month", range(1, 13), index=today.month - 1, format_func=lambda m: calendar.month_name[m])

    period_type = st.radio("Period type", ["Week", "Month"], horizontal=True)
    if period_type == "Week":
        weeks = week_periods(int(year), int(month))
        week_choice = st.selectbox(
            "Week", weeks, format_func=lambda w: f"{w.label} ({w.start:%d %b} - {w.end:%d %b}, {w.days} days)")
        period = week_choice
        period_type_key = "week"
    else:
        period = month_period(int(year), int(month))
        period_type_key = "month"

    scope_choice = st.radio("Scope", ["All regions (management summary + 10 regional reports)", "Selected regions only"], horizontal=True)
    chosen_regions = REGIONS
    if scope_choice == "Selected regions only":
        chosen_regions = st.multiselect("Regions", REGIONS, default=REGIONS[:1])

    with st.expander("Human notes for this period"):
        existing_notes = store.list_notes(engine, period.start, period.end)
        if existing_notes:
            st.dataframe(pd.DataFrame(existing_notes)[["region", "kind", "text", "entered_by"]], use_container_width=True, hide_index=True)
        with st.form("add_note_form", clear_on_submit=True):
            n1, n2 = st.columns(2)
            note_region = n1.selectbox("Region (or ALL)", ["ALL"] + REGIONS)
            note_kind = n2.selectbox("Kind", ["intervention", "provisional", "general"])
            note_text = st.text_area("Note text")
            if st.form_submit_button("Add note") and note_text.strip():
                store.add_note(engine, period.start, period.end, None if note_region == "ALL" else note_region,
                               note_kind, note_text.strip(), user)
                st.rerun()

    with st.expander("Allowance overrides"):
        existing_overrides = store.list_overrides(engine, period.start, period.end)
        if existing_overrides:
            st.dataframe(pd.DataFrame(existing_overrides), use_container_width=True, hide_index=True)
        with st.form("add_override_form", clear_on_submit=True):
            o1, o2 = st.columns(2)
            ov_station = o1.text_input("Station")
            ov_feeder = o2.text_input("Feeder")
            o3, o4 = st.columns(2)
            ov_from = o3.date_input("From", value=period.start)
            ov_to = o4.date_input("To", value=period.end)
            ov_exclude = st.checkbox("Exclude entirely (no allowance)")
            ov_hours = None if ov_exclude else st.number_input("Allowed hours per day", 0.0, 24.0, 4.0)
            ov_reason = st.text_area("Reason (required)")
            if st.form_submit_button("Add override") and ov_station and ov_feeder and ov_reason.strip():
                store.add_override(engine, ov_station, ov_feeder, ov_from, ov_to, ov_hours, ov_exclude, ov_reason.strip(), user)
                st.rerun()

    st.subheader("Signatory")
    s1, s2, s3 = st.columns(3)
    sig_name = s1.text_input("Name", value=st.session_state.get("mlr_sig_name", "Engr. G. A. Aguiyi"))
    sig_title = s2.text_input("Title", value=st.session_state.get("mlr_sig_title", "General Manager (Operations)"))
    sig_org = s3.text_input("Organisation", value=st.session_state.get("mlr_sig_org", "Transmission Company of Nigeria"))
    st.session_state["mlr_sig_name"], st.session_state["mlr_sig_title"], st.session_state["mlr_sig_org"] = sig_name, sig_title, sig_org
    signatory = dict(name=sig_name, title=sig_title, organisation=sig_org)

    if st.button("Generate drafts", type="primary"):
        with st.spinner("Reading outages and building the reports..."):
            sla_map = D.sla_lookup(D.read_sla())
            tariff_map = D.tariff_lookup(D.read_tariff_rates())
            default_rate = D.read_default_tariff()
            df = D.read_outages(period.start, period.end)
            all_tcn_raw = df[df["party_responsible"] == "TCN"].copy()
            all_tcn = all_tcn_raw[~all_tcn_raw["is_open"]]  # closed only, matches the Reliability KPI page's logic
            still_open_this_period = M.still_open_feeders(all_tcn_raw)

            mtd = month_to_date(period.end)
            df_mtd = D.read_outages(mtd.start, mtd.end)
            all_tcn_mtd_raw = df_mtd[df_mtd["party_responsible"] == "TCN"].copy()
            all_tcn_mtd = all_tcn_mtd_raw[~all_tcn_mtd_raw["is_open"]]

            generated = {}
            use_ai = bool(ai_api_key)

            want_management = scope_choice.startswith("All regions")
            want_regions = chosen_regions if scope_choice != "All regions (management summary + 10 regional reports)" else REGIONS

            for region in want_regions:
                tcn = all_tcn[all_tcn["region"] == region]
                tcn_mtd = all_tcn_mtd[all_tcn_mtd["region"] == region]
                compliance = M.sla_compliance(tcn, sla_map, period.days)
                exc = M.exceedance(tcn, sla_map, tariff_map, default_rate, period.days)
                mtd_compliance = M.sla_compliance(tcn_mtd, sla_map, mtd.days)
                mtd_exc = M.exceedance(tcn_mtd, sla_map, tariff_map, default_rate, mtd.days)
                notes = store.list_notes(engine, period.start, period.end, region)
                prev_issued = None
                if period_type_key == "week":
                    all_weeks = week_periods(int(year), int(month))
                    idx = all_weeks.index(period) if period in all_weeks else -1
                    if idx > 0:
                        prev_run = store.get_issued_run(engine, "week", all_weeks[idx - 1].start, all_weeks[idx - 1].end, "ALL")
                        if prev_run:
                            pf = prev_run["facts"]
                            cbr = pf.get("cost_by_region", {}).get(region)
                            # cost_by_region is per-region (accurate for a comparison); the seeded
                            # snapshot only has a network-wide outage count, not a per-region one,
                            # so "outages" is left out here rather than showing a misleading figure.
                            if cbr is not None:
                                prev_issued = dict(period_label=all_weeks[idx - 1].label, estimated_cost_ngn=cbr)
                facts = M.build_facts(region, period.label, period.start, period.end, tcn, None, compliance, exc,
                                      mtd_exc, mtd_compliance, prev_issued, notes)
                text, ai_notes = (N.with_llm(facts, "regional", ai_provider or "anthropic", ai_api_key, ai_model)
                                  if use_ai else (N.rule_based(facts, "regional"), []))
                tables = dict(top_stations=M.top_stations(tcn).to_dict("records"),
                             top_feeders=M.top_feeders(tcn).to_dict("records"),
                             compliance=compliance.to_dict("records"), exceedance=exc.to_dict("records"))
                docx_bytes = B.build_docx("regional", facts, tables, text, signatory)
                generated[region] = dict(facts=facts, text=text, notes=ai_notes, docx=docx_bytes, scope=region)

            if want_management:
                compliance = M.sla_compliance(all_tcn, sla_map, period.days)
                exc = M.exceedance(all_tcn, sla_map, tariff_map, default_rate, period.days)
                mtd_compliance = M.sla_compliance(all_tcn_mtd, sla_map, mtd.days)
                mtd_exc = M.exceedance(all_tcn_mtd, sla_map, tariff_map, default_rate, mtd.days)
                notes = store.list_notes(engine, period.start, period.end, None)
                facts = M.build_facts(None, period.label, period.start, period.end, all_tcn, df, compliance, exc,
                                      mtd_exc, mtd_compliance, None, notes,
                                      still_open=still_open_this_period.to_dict("records"))
                text, ai_notes = (N.with_llm(facts, "management", ai_provider or "anthropic", ai_api_key, ai_model)
                                  if use_ai else (N.rule_based(facts, "management"), []))
                mtd_top = M.top_cost_feeders(mtd_exc, 10)
                comparative = _comparative_tables(engine, period_type_key, period, facts, REGIONS)
                tables = dict(
                    **comparative,
                    mtd_exceedance=mtd_exc.to_dict("records"),
                    mtd_top_cost_rows=[[r["station"], r["feeder"], f"{r['tcn_hrs']:.2f}", f"{r['excess_hrs']:.2f}",
                                        f"{r['excess_load_loss_mwh']:.2f}", f"₦{r['estimated_cost_ngn']:,.2f}"]
                                       for r in mtd_top.to_dict("records")],
                )
                docx_bytes = B.build_docx("management", facts, tables, text, signatory)
                generated["ALL"] = dict(facts=facts, text=text, notes=ai_notes, docx=docx_bytes, scope="ALL")

        st.session_state["sla_generated"] = generated
        st.session_state["sla_generated_period"] = dict(
            period_type=period_type_key, period_start=period.start, period_end=period.end, period_label=period.label)

    generated = st.session_state.get("sla_generated")
    gp = st.session_state.get("sla_generated_period")
    if generated and gp and gp["period_start"] == period.start and gp["period_end"] == period.end:
        st.success(f"Generated {len(generated)} document(s) for {period.label}.")
        run_ids = []
        for scope, doc in generated.items():
            label = "Management Summary" if scope == "ALL" else scope
            with st.expander(f"{label} -- {doc['facts']['outages']} outages, ₦{doc['facts']['estimated_cost_ngn']:,.2f}"):
                for n in doc["notes"]:
                    st.caption(n)
                st.download_button("Download .docx", doc["docx"], f"{label.replace(' ', '_')}_{period.label.replace(' ', '_')}.docx",
                                   key=f"dl_{scope}")
                if st.button("Save & mark as issued", key=f"issue_{scope}"):
                    rid = store.save_run(engine, gp["period_type"], gp["period_start"], gp["period_end"], gp["period_label"],
                                         scope, doc["facts"], doc["text"], doc["notes"], ai_model if ai_api_key else "rules",
                                         user, doc["docx"])
                    store.mark_issued(engine, [rid], user)
                    log_activity("sla_report_issued", f"{gp['period_label']} -- {label}, run #{rid}")
                    st.success(f"Saved and marked issued as run #{rid}.")

        zip_data = zip_bytes({f"{('Management_Summary' if s=='ALL' else s).replace(' ', '_')}.docx": d["docx"] for s, d in generated.items()})
        st.download_button("Download all as .zip", zip_data, f"SLA_Reports_{period.label.replace(' ', '_')}.zip")

with tab_history:
    runs = store.list_runs(engine)
    if not runs:
        st.write("No runs saved yet.")
    else:
        df_runs = pd.DataFrame(runs)
        st.dataframe(df_runs, use_container_width=True, hide_index=True)
        pick = st.selectbox("Run", df_runs.id, format_func=lambda i: (
            f"#{i} · {df_runs.set_index('id').loc[i, 'period_label']} · {df_runs.set_index('id').loc[i, 'scope']} · "
            f"{df_runs.set_index('id').loc[i, 'status']}"))
        row = df_runs.set_index("id").loc[pick]
        docx_bytes = store.get_run_docx(engine, int(pick))
        if docx_bytes:
            st.download_button("Download .docx", docx_bytes, f"run_{pick}.docx")
        if row["status"] == "draft":
            if st.button("Mark as issued", key=f"hist_issue_{pick}"):
                store.mark_issued(engine, [int(pick)], user)
                st.rerun()
