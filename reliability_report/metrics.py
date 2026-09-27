"""Pure functions: DataFrame/dict in, DataFrame/dict out. No Streamlit, no
database, no python-docx. This is the SLA exceedance/cost engine, adapted
from views/reliability_kpi_report.py's existing (already-shipped, already
correct) exceedance section -- same math, generalized to any period and
labeled with the band actually used (including "assumed").
"""
from __future__ import annotations

import pandas as pd

from .data import BAND_HOURS, DEFAULT_BAND, TCN_SHARE, get_rate


def headline_kpis(df: pd.DataFrame) -> dict:
    """Every outage in df, whatever the party -- used for the management
    summary's region-by-region section, which counts all parties."""
    if df.empty:
        return dict(outages=0, hours=0.0, load_loss_mwh=0.0, regions=0)
    return dict(
        outages=len(df),
        hours=round(float(df["duration_hr"].sum()), 2),
        load_loss_mwh=round(float(df["load_loss_mwh"].sum()), 2),
        regions=int(df["region"].nunique()),
    )


def cause_share(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    counts = df["outage_class"].fillna("Unspecified").value_counts()
    total = counts.sum()
    return [dict(cause=c, share_pct=round(100 * n / total, 1)) for c, n in counts.items()]


def region_summary(df: pd.DataFrame) -> pd.DataFrame:
    """All parties -- spec: "Management section 2 counts all outages
    whatever the party."""
    if df.empty:
        return pd.DataFrame(columns=["region", "outages", "total_hrs", "avg_duration_min", "load_loss_mwh"])
    g = df.groupby("region").agg(
        outages=("id", "count"),
        total_hrs=("duration_hr", "sum"),
        load_loss_mwh=("load_loss_mwh", "sum"),
    ).reset_index()
    g["avg_duration_min"] = (g["total_hrs"] * 60 / g["outages"]).round(2)
    g["total_hrs"] = g["total_hrs"].round(2)
    g["load_loss_mwh"] = g["load_loss_mwh"].round(2)
    return g.sort_values("total_hrs", ascending=False)


def top_stations(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["station", "outages", "total_min", "avg_min", "load_loss_mwh", "avg_load_loss_mwh"])
    g = df.groupby("station").agg(
        outages=("id", "count"),
        total_min=("duration_hr", lambda x: round(x.sum() * 60, 2)),
        load_loss_mwh=("load_loss_mwh", lambda x: round(x.sum(), 2)),
    ).reset_index()
    g["avg_min"] = (g["total_min"] / g["outages"]).round(2)
    g["avg_load_loss_mwh"] = (g["load_loss_mwh"] / g["outages"]).round(2)
    return g.sort_values("total_min", ascending=False).head(n)


def top_feeders(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["station", "feeder", "outages", "total_hrs", "avg_hrs", "load_loss_mwh"])
    g = df.groupby(["station", "feeder_33kv"]).agg(
        outages=("id", "count"),
        total_hrs=("duration_hr", lambda x: round(x.sum(), 2)),
        load_loss_mwh=("load_loss_mwh", lambda x: round(x.sum(), 2)),
    ).reset_index().rename(columns={"feeder_33kv": "feeder"})
    g["avg_hrs"] = (g["total_hrs"] / g["outages"]).round(2)
    return g.sort_values("total_hrs", ascending=False).head(n)


def _band_for(station_norm, feeder_norm, disco_norm, sla_map) -> tuple[str, bool]:
    """(band, is_assumed). Missing from the SLA table, or present with a
    blank band, both fall back to Band A (assumed) -- sla_map only holds
    entries that DO have a real band, so a miss covers both cases."""
    hit = sla_map.get((station_norm, feeder_norm))
    if hit:
        return hit[0], False
    return DEFAULT_BAND, True


def sla_compliance(df_tcn: pd.DataFrame, sla_map: dict, days: int) -> pd.DataFrame:
    """One row per (station, feeder) with TCN hours in the period against
    its allocation -- TCN's allocation is 30% of the band's daily allowed
    outage hours x days in the period (spec's TCN_SHARE rule)."""
    if df_tcn.empty:
        return pd.DataFrame(columns=["station", "feeder", "disco", "band", "band_assumed",
                                      "tcn_hrs", "allocation_hrs", "available_hrs"])
    g = df_tcn.groupby(["station", "feeder_33kv", "station_norm", "feeder_norm", "disco_norm"]).agg(
        tcn_hrs=("duration_hr", "sum"),
    ).reset_index().rename(columns={"feeder_33kv": "feeder", "disco_norm": "disco"})

    bands, assumed = [], []
    for r in g.itertuples():
        b, a = _band_for(r.station_norm, r.feeder_norm, r.disco, sla_map)
        bands.append(b)
        assumed.append(a)
    g["band"] = bands
    g["band_assumed"] = assumed
    g["allocation_hrs"] = (g["band"].map(BAND_HOURS) * days * TCN_SHARE).round(2)
    g["tcn_hrs"] = g["tcn_hrs"].round(2)
    g["available_hrs"] = (g["allocation_hrs"] - g["tcn_hrs"]).round(2)
    return g.drop(columns=["station_norm", "feeder_norm"]).sort_values("available_hrs")


def exceedance(df_tcn: pd.DataFrame, sla_map: dict, tariff_map: dict, default_rate: float, days: int) -> pd.DataFrame:
    """Feeders over their allocation, with excess hours worked out in time
    order (a feeder's TCN outages use up its allocation in the order they
    happened -- only hours after the allocation runs out are excess) and
    cost from each excess hour's own recorded load. Cost = excess MWh x
    1000 x the feeder's (disco, band) tariff, Band A tariff if assumed."""
    comp = sla_compliance(df_tcn, sla_map, days)
    over = comp[comp["available_hrs"] < 0].copy()
    if over.empty:
        return over.assign(excess_hrs=[], excess_load_loss_mwh=[], tariff_rate_ngn_per_kwh=[], estimated_cost_ngn=[])

    events = df_tcn.sort_values(["clipped_start", "id"])
    grouped = {key: g for key, g in events.groupby(["station", "feeder_33kv"])}

    def _excess_mwh(station, feeder, allocation_hrs):
        grp = grouped.get((station, feeder))
        if grp is None or grp.empty:
            return 0.0
        running, excess_mwh = 0.0, 0.0
        for ev in grp.itertuples():
            hours = 0.0 if pd.isna(ev.duration_hr) else float(ev.duration_hr)
            load = 0.0 if pd.isna(ev.last_load) else float(ev.last_load)
            new_running = running + hours
            if running >= allocation_hrs:
                excess_this = hours
            elif new_running > allocation_hrs:
                excess_this = new_running - allocation_hrs
            else:
                excess_this = 0.0
            excess_mwh += excess_this * load
            running = new_running
        return excess_mwh

    over["excess_hrs"] = (-over["available_hrs"]).round(2)
    over["excess_load_loss_mwh"] = over.apply(
        lambda r: round(_excess_mwh(r["station"], r["feeder"], r["allocation_hrs"]), 2), axis=1)
    over["tariff_rate_ngn_per_kwh"] = over.apply(
        lambda r: get_rate(r["disco"], r["band"], tariff_map, default_rate), axis=1)
    over["estimated_cost_ngn"] = (over["excess_load_loss_mwh"] * 1000 * over["tariff_rate_ngn_per_kwh"]).round(2)
    return over.sort_values("estimated_cost_ngn", ascending=False)


def top_cost_feeders(exceedance_df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    if exceedance_df.empty:
        return exceedance_df
    return exceedance_df.sort_values("estimated_cost_ngn", ascending=False).head(n)


def exceedance_by_region(df_tcn: pd.DataFrame, exceedance_df: pd.DataFrame) -> pd.DataFrame:
    """Region-level rollup of the exceedance table -- station->region
    comes from df_tcn, joined back on since exceedance_df is per feeder."""
    if exceedance_df.empty:
        return pd.DataFrame(columns=["region", "feeders_over", "excess_hrs", "excess_load_loss_mwh", "estimated_cost_ngn"])
    station_region = df_tcn[["station", "region"]].drop_duplicates().set_index("station")["region"]
    ex = exceedance_df.copy()
    ex["region"] = ex["station"].map(station_region)
    g = ex.groupby("region").agg(
        feeders_over=("station", "count"),
        excess_hrs=("excess_hrs", "sum"),
        excess_load_loss_mwh=("excess_load_loss_mwh", "sum"),
        estimated_cost_ngn=("estimated_cost_ngn", "sum"),
    ).reset_index()
    for c in ("excess_hrs", "excess_load_loss_mwh", "estimated_cost_ngn"):
        g[c] = g[c].round(2)
    return g.sort_values("estimated_cost_ngn", ascending=False)


def _events_for_facts(events_df: pd.DataFrame, station, feeder, max_events: int = 10) -> list[dict]:
    grp = events_df[(events_df["station"] == station) & (events_df["feeder_33kv"] == feeder)]
    out = []
    for r in grp.sort_values("clipped_start").itertuples():
        out.append(dict(
            date_off=str(r.date_off), time_off=str(r.time_off), date_on=str(r.date_on) if pd.notna(r.date_on) else None,
            time_on=str(r.time_on) if pd.notna(r.time_on) else None, hours=round(float(r.duration_hr), 2),
            last_load=None if pd.isna(r.last_load) else float(r.last_load), outage_class=r.outage_class,
            equipment=r.equipment, event_indication=r.event_indication,
            remarks=(str(r.remarks)[:400] if pd.notna(r.remarks) else None),
        ))
    return out[:max_events]


def build_facts(region: str | None, period_label: str, period_start, period_end, df_tcn: pd.DataFrame,
                 df_all: pd.DataFrame, compliance: pd.DataFrame, exc: pd.DataFrame, mtd_exc: pd.DataFrame,
                 mtd_compliance: pd.DataFrame, previous_issued: dict | None, human_notes: list[dict],
                 top_n: int = 15) -> dict:
    """region=None means the management summary (all regions, TCN-only for
    SLA/exceedance/cost, all parties for the headline/region_summary
    figures); a region name means that regional report (TCN-only
    throughout). Includes each top-cost/repeat-offender feeder's own
    outage events (remarks capped at 400 chars) for the narrative's
    root-cause writing, and at-risk feeders from the month-to-date
    compliance table."""
    kpis = headline_kpis(df_tcn)
    top_cost = top_cost_feeders(exc, top_n)
    at_risk = at_risk_feeders(mtd_compliance)

    facts = dict(
        region=region or "ALL", period_label=period_label, period_start=str(period_start), period_end=str(period_end),
        outages=kpis["outages"], hours=kpis["hours"], load_loss_mwh=kpis["load_loss_mwh"],
        feeders_over_allocation=len(exc),
        excess_hrs=round(float(exc["excess_hrs"].sum()), 2) if not exc.empty else 0.0,
        excess_load_loss_mwh=round(float(exc["excess_load_loss_mwh"].sum()), 2) if not exc.empty else 0.0,
        estimated_cost_ngn=round(float(exc["estimated_cost_ngn"].sum()), 2) if not exc.empty else 0.0,
        cause_share=cause_share(df_tcn),
        top_cost_feeders=[
            dict(station=r["station"], feeder=r["feeder"], band=r["band"], band_assumed=bool(r["band_assumed"]),
                 tcn_hrs=r["tcn_hrs"], excess_hrs=r["excess_hrs"], excess_load_loss_mwh=r["excess_load_loss_mwh"],
                 estimated_cost_ngn=r["estimated_cost_ngn"],
                 events=_events_for_facts(df_tcn, r["station"], r["feeder"]))
            for r in top_cost.to_dict("records")
        ],
        at_risk_feeders=[
            dict(station=r["station"], feeder=r["feeder"], band=r["band"], band_assumed=bool(r["band_assumed"]),
                 used_pct=r["used_pct"], tcn_hrs=r["tcn_hrs"], allocation_hrs=r["allocation_hrs"])
            for r in at_risk.to_dict("records")
        ],
        assumed_band_feeders=[
            dict(station=r["station"], feeder=r["feeder"])
            for r in compliance[compliance["band_assumed"]].to_dict("records")
        ],
        at_risk_threshold_pct=70,
        human_notes=human_notes,
        previous_issued=previous_issued,
    )
    if region is None:
        rs = region_summary(df_all)
        facts["regions"] = int(rs["outages"].count()) if not rs.empty else 0
        facts["regions_with_exceedance"] = int(exc["station"].map(
            df_tcn.drop_duplicates("station").set_index("station")["region"]).nunique()) if not exc.empty else 0
        facts["region_summary"] = rs.to_dict("records")
        facts["exceedance_by_region"] = exceedance_by_region(df_tcn, exc).to_dict("records")
        top_cause = max(facts["cause_share"], key=lambda c: c["share_pct"]) if facts["cause_share"] else None
        facts["top_cause"] = (top_cause["cause"], top_cause["share_pct"]) if top_cause else None
        facts["mtd_estimated_cost_ngn"] = round(float(mtd_exc["estimated_cost_ngn"].sum()), 2) if not mtd_exc.empty else 0.0
    return facts


def at_risk_feeders(mtd_compliance: pd.DataFrame, threshold: float = 0.70) -> pd.DataFrame:
    """TCN feeders that have used >= threshold of their month-to-date
    allocation but are not yet over it -- where money can still be saved
    this month."""
    if mtd_compliance.empty:
        return mtd_compliance.assign(used_pct=[])
    df = mtd_compliance[mtd_compliance["available_hrs"] >= 0].copy()
    df["used_pct"] = (df["tcn_hrs"] / df["allocation_hrs"]).round(3)
    return df[df["used_pct"] >= threshold].sort_values("used_pct", ascending=False)
