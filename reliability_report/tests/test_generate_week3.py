"""Generates Week 3 (13-19 Sept 2026) for Enugu and the Management Summary
into data/samples/output/, exercising the full pipeline: data -> metrics ->
narrative -> docx_builder. Prompt 5's own verification ask.

Run from outage_tracker-main/: ./venv/bin/python reliability_report/tests/test_generate_week3.py
"""
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from dotenv import load_dotenv
load_dotenv()

from reliability_report import data as D, metrics as M, narrative as N, docx_builder as B, store
from reliability_report.periods import week_periods, month_to_date

PS, PE = dt.date(2026, 9, 13), dt.date(2026, 9, 19)
DAYS = (PE - PS).days + 1
SIGNATORY = dict(name="Engr. G. A. Aguiyi", title="General Manager (Operations)",
                 organisation="Transmission Company of Nigeria")
OUT = Path(__file__).resolve().parent.parent.parent / "data" / "samples" / "output"

api_key = os.getenv("AI_API_KEY")
model = os.getenv("AI_MODEL")
provider = os.getenv("AI_PROVIDER") or "anthropic"


def _records(df):
    return df.to_dict("records") if not df.empty else []


def build_region(engine, region: str):
    df = D.read_outages(PS, PE)
    tcn_raw = df[(df["party_responsible"] == "TCN") & (df["region"] == region)].copy()
    tcn = tcn_raw[~tcn_raw["is_open"]]
    sla_map = D.sla_lookup(D.read_sla())
    tariff_map = D.tariff_lookup(D.read_tariff_rates())
    default_rate = D.read_default_tariff()

    compliance = M.sla_compliance(tcn, sla_map, DAYS)
    exc = M.exceedance(tcn, sla_map, tariff_map, default_rate, DAYS)

    mtd = month_to_date(PE)
    df_mtd = D.read_outages(mtd.start, mtd.end)
    tcn_mtd_raw = df_mtd[(df_mtd["party_responsible"] == "TCN") & (df_mtd["region"] == region)]
    tcn_mtd = tcn_mtd_raw[~tcn_mtd_raw["is_open"]]
    mtd_compliance = M.sla_compliance(tcn_mtd, sla_map, mtd.days)
    mtd_exc = M.exceedance(tcn_mtd, sla_map, tariff_map, default_rate, mtd.days)

    prev_week = week_periods(2026, 9)[1]  # Week 2
    prev_run = store.get_issued_run(engine, "week", prev_week.start, prev_week.end, "ALL")
    prev_issued = None
    if prev_run:
        prev_issued = dict(period_label="Week 2",
                            outages=prev_run["facts"].get("cost_by_region", {}).get(region, (0, 0)),
                            estimated_cost_ngn=prev_run["facts"].get("cost_by_region", {}).get(region, (0, 0))[1]
                            if isinstance(prev_run["facts"].get("cost_by_region", {}).get(region), (list, tuple)) else 0)

    notes = store.list_notes(engine, PS, PE, region)
    facts = M.build_facts(region, "Week 3", PS, PE, tcn, None, compliance, exc, mtd_exc, mtd_compliance,
                          previous_issued=prev_issued, human_notes=notes)

    if api_key:
        text, ai_notes = N.with_llm(facts, "regional", provider, api_key, model)
    else:
        text = N.rule_based(facts, "regional")
        ai_notes = ["no AI_API_KEY set -- rule-based text used"]

    tables = dict(top_stations=_records(M.top_stations(tcn)), top_feeders=_records(M.top_feeders(tcn)),
                  compliance=_records(compliance), exceedance=_records(exc))
    docx_bytes = B.build_docx("regional", facts, tables, text, SIGNATORY)
    out_path = OUT / f"{region.replace(' ', '_')}_Week3_generated.docx"
    out_path.write_bytes(docx_bytes)
    print(f"{region}: wrote {out_path} ({len(docx_bytes):,} bytes), AI notes: {ai_notes}")
    return facts


def build_management(engine):
    df = D.read_outages(PS, PE)
    tcn_raw = df[df["party_responsible"] == "TCN"].copy()
    tcn = tcn_raw[~tcn_raw["is_open"]]
    sla_map = D.sla_lookup(D.read_sla())
    tariff_map = D.tariff_lookup(D.read_tariff_rates())
    default_rate = D.read_default_tariff()

    compliance = M.sla_compliance(tcn, sla_map, DAYS)
    exc = M.exceedance(tcn, sla_map, tariff_map, default_rate, DAYS)

    mtd = month_to_date(PE)
    df_mtd = D.read_outages(mtd.start, mtd.end)
    tcn_mtd_raw = df_mtd[df_mtd["party_responsible"] == "TCN"]
    tcn_mtd = tcn_mtd_raw[~tcn_mtd_raw["is_open"]]
    mtd_compliance = M.sla_compliance(tcn_mtd, sla_map, mtd.days)
    mtd_exc = M.exceedance(tcn_mtd, sla_map, tariff_map, default_rate, mtd.days)
    still_open = M.still_open_feeders(tcn_raw)

    notes = store.list_notes(engine, PS, PE, None)
    facts = M.build_facts(None, "Week 3", PS, PE, tcn, df, compliance, exc, mtd_exc, mtd_compliance,
                          still_open=still_open.to_dict("records"),
                          previous_issued=None, human_notes=notes)

    if api_key:
        text, ai_notes = N.with_llm(facts, "management", provider, api_key, model)
    else:
        text = N.rule_based(facts, "management")
        ai_notes = ["no AI_API_KEY set -- rule-based text used"]

    weeks = week_periods(2026, 9)
    issued = []
    comparative_rows, comparative_region_rows = [], []
    for w in weeks[:3]:
        run = store.get_issued_run(engine, "week", w.start, w.end, "ALL")
        if run:
            issued.append(dict(period_label=w.label, facts=run["facts"]))
    if len(issued) >= 2:
        issued.append(dict(period_label="Week 3", facts=facts))
        for metric_key, label in [("outages", "Total Outages"), ("hours", "Total Outage Hours"),
                                   ("estimated_cost_ngn", "Estimated Cost")]:
            comparative_rows.append([label] + [f"{p['facts'].get(metric_key, 0):,}" for p in issued])
        regions = sorted({r for p in issued for r in p["facts"].get("cost_by_region", {})})
        for r in regions:
            row = [r]
            for p in issued[:2]:
                v = p["facts"].get("cost_by_region", {}).get(r)
                row.append(f"₦{v[1] if isinstance(v, (list, tuple)) else 0:,.2f}")
            row.append(f"₦{facts.get('estimated_cost_ngn', 0):,.2f}" if r == "Enugu" else "")
            comparative_region_rows.append(row)

    mtd_top = M.top_cost_feeders(mtd_exc, 10)
    tables = dict(
        issued_periods=[dict(period_label=p["period_label"]) for p in issued],
        comparative_rows=comparative_rows, comparative_region_rows=comparative_region_rows,
        mtd_exceedance=_records(mtd_exc),
        mtd_top_cost_rows=[[r["station"], r["feeder"], f"{r['tcn_hrs']:.2f}", f"{r['excess_hrs']:.2f}",
                            f"{r['excess_load_loss_mwh']:.2f}", f"₦{r['estimated_cost_ngn']:,.2f}"]
                           for r in mtd_top.to_dict("records")],
    )
    docx_bytes = B.build_docx("management", facts, tables, text, SIGNATORY)
    out_path = OUT / "Management_Summary_Week3_generated.docx"
    out_path.write_bytes(docx_bytes)
    print(f"Management Summary: wrote {out_path} ({len(docx_bytes):,} bytes), AI notes: {ai_notes}")


if __name__ == "__main__":
    from utils.db import get_engine
    engine = get_engine()
    OUT.mkdir(parents=True, exist_ok=True)
    build_region(engine, "Enugu")
    build_management(engine)
