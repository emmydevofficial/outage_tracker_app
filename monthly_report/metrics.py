"""Turn parsed rows into every figure the monthly review needs.

All numbers in the report come from here. The AI only writes sentences
about these numbers; it never calculates them. Each rule below is written
out so an engineer can check it.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from statistics import mean

import pandas as pd

POWER_FACTOR = 0.9            # loading = MW / (MVA x 0.9)
ZERO_FEEDER_MW = 0.2          # a feeder minimum at or below this is treated as "went to zero"
VOLT_BANDS = {330: 0.05, 132: 0.10}   # Grid Code normal-operation band, confirm edition
VOLT_SANITY = (0.75, 1.30)    # outside 75%-130% of nominal is treated as an entry error
BOUNDARY_GAP_FLAG = 0.10      # interconnector maxima differing by more than 10%

STATUS_ORDER = [
    "Reporting", "In service, no separate reading", "Energised / standby, no load",
    "New / on soak / not connected", "Maintenance / works", "Faulty / tripped",
    "Out of service", "Decommissioned / spare", "No data",
]
UNAVAILABLE = {"Maintenance / works", "Faulty / tripped", "Out of service"}
LOAD_BANDS = [("under 40%", 0, 40), ("40–60%", 40, 60), ("60–80%", 60, 80),
              ("80–90%", 80, 90), ("90% and above", 90, 10_000)]


# ---------------------------------------------------------------- helpers
def _words(*parts) -> str:
    return " ".join(str(p) for p in parts if p).upper()


def transformer_status(r: dict) -> str:
    t = _words(r.get("status_text"), r.get("remarks"))
    t_no_meter = re.sub(r"FAU?LTY (ENERGY )?METER|FAULTY WT GAUGE|NO (ENERGY )?METER|METER NOT INSTALLED|UNAVAILABLE METER", "", t)
    has_max = r.get("max_mw") is not None
    if re.search(r"DECOMM?ISS|REMOVED|\bSPARE\b", t_no_meter):
        return "Decommissioned / spare"
    if re.search(r"FAULT|BURNT|TRIP|\bT/?F\b|DIFFERENTI", t_no_meter):
        if not (has_max and r["max_mw"] > 0):
            return "Faulty / tripped"
    if re.search(r"\bMTCE|WORK|\bWO\b|MAINTENANCE|REHABILITATION", t_no_meter) and not has_max:
        return "Maintenance / works"
    if re.search(r"\bO/S\b|\bOUT\b|NOT IN SERV", t_no_meter) and not (has_max and r["max_mw"] > 0):
        return "Out of service"
    if re.search(r"SOAK|NOT CONNECTED|NOT YET INSTALLED|UNDER INSTALLATION|\bNEW\b", t_no_meter) and not (has_max and r["max_mw"] > 0):
        return "New / on soak / not connected"
    if has_max and r["max_mw"] > 0:
        return "Reporting"
    if re.search(r"POTENTIAL|STANDBY|REDUNDAN", t_no_meter) or (has_max and r["max_mw"] == 0):
        return "Energised / standby, no load"
    if re.search(r"\bI/S\b|IN SERVICE|\bON\b", t_no_meter):
        return "In service, no separate reading"
    return "No data"


def voltage_ratio(*texts) -> str | None:
    for s in texts:
        if not s:
            continue
        m = re.search(r"(330|132|33)\s*/\s*(132|33|11)", str(s))
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    return None


def substation_key(sub: str | None) -> str:
    s = (sub or "").upper()
    s = re.sub(r"\d{2,3}\s*(/\s*\d{2,3})*\s*K?V", " ", s)
    s = re.sub(r"\b(T/S|TS|SUBSTATION|S/S|KV|TRANSMISSION STATION)\b", " ", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip().title() or "Unknown"


def line_nominal_kv(name: str, kv: float | None) -> int | None:
    m = re.search(r"(330|132)\s*K?V", name or "", re.I)
    if m:
        return int(m.group(1))
    if kv:
        return 330 if kv > 200 else 132 if kv > 60 else None
    return None


def hour_of(t: str | None) -> int | None:
    return int(t[:2]) if isinstance(t, str) and t else None


# ---------------------------------------------------------------- main
def compute(rows: list[dict], issues: list[dict], month_label: str) -> dict:
    df = pd.DataFrame(rows)
    regions = sorted(df.region.unique())
    tx = df[df.kind == "transformer"].copy()
    fd = df[df.kind == "feeder"].copy()
    ln = df[df.kind == "line"].copy()

    # ---- transformers: status and availability
    tx["status"] = [transformer_status(r) for r in tx.to_dict("records")]
    tx["ratio"] = [voltage_ratio(n, s) for n, s in zip(tx.name, tx.substation)]
    tx["sub_key"] = tx.substation.map(substation_key)
    tx["loading_pct"] = tx.apply(
        lambda r: 100 * r.max_mw / (r.rating_mva * POWER_FACTOR)
        if pd.notna(r.max_mw) and pd.notna(r.rating_mva) and r.rating_mva > 0 and r.status == "Reporting" else None, axis=1)
    # a loading above 150% is almost always an MW/MVA entry mistake: keep it out of the ranking, count it
    tx["loading_suspect"] = tx.loading_pct.apply(lambda v: v is not None and pd.notna(v) and v > 150)
    tx["hot_temp"] = tx[["temp_primary_max", "temp_secondary_max"]].apply(
        lambda s: max([v for v in s if pd.notna(v) and 0 < v < 150], default=None), axis=1)

    status_by_region = {rg: {s: int(((tx.region == rg) & (tx.status == s)).sum()) for s in STATUS_ORDER} for rg in regions}
    unavail = tx[tx.status.isin(UNAVAILABLE)]
    mva_unavail = unavail.groupby("region").rating_mva.sum().reindex(regions, fill_value=0)

    loaded = tx[tx.loading_pct.notna() & ~tx.loading_suspect]
    bands = {rg: {b: int(((loaded.region == rg) & (loaded.loading_pct >= lo) & (loaded.loading_pct < hi)).sum())
                  for b, lo, hi in LOAD_BANDS} for rg in regions}
    top_loaded = loaded.sort_values("loading_pct", ascending=False).head(20)

    # ---- N-1 screen per substation and ratio
    n1_fail, single = [], []
    in_serv = tx[(tx.status == "Reporting") & tx.rating_mva.notna() & tx.max_mw.notna() & tx.ratio.notna()]
    groups = in_serv.groupby(["region", "sub_key", "ratio"])
    multi_count = 0
    for (rg, sub, ratio), g in groups:
        if len(g) >= 2:
            multi_count += 1
            firm = (g.rating_mva.sum() - g.rating_mva.max()) * POWER_FACTOR
            peak = g.max_mw.sum()
            identical = g[["max_mw", "max_time", "max_date"]].astype(str).duplicated().any()
            if peak > firm:
                n1_fail.append(dict(region=rg, substation=sub, ratio=ratio, units=len(g), firm_mw=round(firm, 1),
                                    peak_mw=round(peak, 1), over_mw=round(peak - firm, 1), identical_entries=bool(identical)))
        else:
            r = g.iloc[0]
            single.append(dict(region=rg, substation=sub, ratio=ratio, mva=r.rating_mva, peak_mw=r.max_mw,
                               loading_pct=None if pd.isna(r.loading_pct) else round(r.loading_pct)))
    n1_fail.sort(key=lambda d: -d["over_mw"])
    single.sort(key=lambda d: (-(d["mva"] or 0), -(d["peak_mw"] or 0)))

    # ---- lines: flows and voltages
    ln["nominal"] = [line_nominal_kv(n, k) for n, k in zip(ln.name, ln.max_kv)]
    ln["amps_check"] = ln.apply(lambda r: _amps_mismatch(r.max_mw, r.max_amps, r.max_kv), axis=1)
    excursions = []
    for r in ln.to_dict("records"):
        nom = r["nominal"]
        if nom not in VOLT_BANDS:
            continue
        band = VOLT_BANDS[nom]
        for when, kv in (("at peak load", r["max_kv"]), ("at minimum load", r["min_kv"])):
            if kv is None or pd.isna(kv):
                continue
            ratio = kv / nom
            if not (VOLT_SANITY[0] <= ratio <= VOLT_SANITY[1]):
                continue
            if abs(ratio - 1) > band + 1e-9:
                excursions.append(dict(region=r["region"], line=r["name"], kv=kv, nominal=nom, when=when,
                                       direction="High" if ratio > 1 else "Low"))
    exc_by_region = {rg: {"High": sum(1 for e in excursions if e["region"] == rg and e["direction"] == "High"),
                          "Low": sum(1 for e in excursions if e["region"] == rg and e["direction"] == "Low")} for rg in regions}
    v330 = [dict(region=r["region"], line=r["name"], peak_kv=r["max_kv"], min_kv=r["min_kv"])
            for r in ln[(ln.nominal == 330) & ln.max_kv.notna() & ln.min_kv.notna()].to_dict("records")
            if 0.75 <= r["max_kv"] / 330 <= 1.3 and 0.75 <= r["min_kv"] / 330 <= 1.3]
    top_flows = [dict(region=r["region"], line=r["name"], mw=r["max_mw"], check=bool(r["amps_check"]))
                 for r in ln[ln.max_mw.notna()].sort_values("max_mw", ascending=False).head(18).to_dict("records")]

    # ---- boundary lines reported by two regions
    ln["nom_key"] = ln.nomenclature.fillna("").str.upper().str.replace(r"[\s_]", "", regex=True)
    boundary = []
    for key, g in ln[(ln.nom_key != "") & ln.max_mw.notna()].groupby("nom_key"):
        per = g.sort_values("max_mw", ascending=False).drop_duplicates("region")
        if per.region.nunique() < 2:
            continue
        a, b = per.iloc[0], per.iloc[1]
        big = max(a.max_mw, b.max_mw)
        gap = abs(a.max_mw - b.max_mw)
        first, second = sorted([a, b], key=lambda x: x.region)
        boundary.append(dict(line=first["name"], code=first.nomenclature, region_a=first.region, mw_a=first.max_mw,
                             region_b=second.region, mw_b=second.max_mw, gap_mw=round(gap, 1),
                             gap_pct=round(100 * gap / big) if big else 0,
                             kv_a=first.max_kv, kv_b=second.max_kv, flag=bool(big and gap / big > BOUNDARY_GAP_FLAG)))
    boundary.sort(key=lambda d: -d["gap_pct"])

    # ---- feeders
    fnum = fd[fd.max_mw.notna()]
    fdip = fd[fd.min_mw.notna()]
    dip_share = {rg: _pct((fdip[fdip.region == rg].min_mw <= ZERO_FEEDER_MW).sum(), (fdip.region == rg).sum()) for rg in regions}
    dip_total = int((fdip.min_mw <= ZERO_FEEDER_MW).sum())
    hist_edges = [i * 2.5 for i in range(0, 14)]
    hist = []
    for lo in hist_edges:
        n = int(((fnum.max_mw >= lo) & (fnum.max_mw < lo + 2.5)).sum())
        if n or lo < 30:
            hist.append(dict(label=f"{lo:g}–{lo + 2.5:g}", n=n))
    while hist and hist[-1]["n"] == 0:
        hist.pop()

    # ---- timing
    hours = {rg: Counter(h for h in fd[fd.region == rg].max_time.map(hour_of) if h is not None) for rg in regions}
    hour_grid = {rg: [hours[rg].get(h, 0) for h in range(24)] for rg in regions}
    all_hours = Counter(h for h in fd.max_time.map(hour_of) if h is not None)
    evening = sum(all_hours.get(h, 0) for h in (19, 20, 21))
    days = {k: Counter(d.day for d in df[(df.kind == k) & df.max_date.notna()].max_date) for k in ("feeder", "transformer", "line")}
    last_day = max([max(c) for c in days.values() if c] or [31])
    day_grid = {k: [days[k].get(d, 0) for d in range(1, last_day + 1)] for k in days}

    # ---- energy meters (baseline this month, consumption from next month)
    meters = {}
    for k, g in (("feeder", fd), ("transformer", tx), ("line", ln)):
        meters[k] = {rg: dict(with_reading=int(g[(g.region == rg)].energy_reading.notna().sum()),
                              total=int((g.region == rg).sum())) for rg in regions}

    # ---- data quality
    iss = pd.DataFrame(issues) if issues else pd.DataFrame(columns=["region", "problem"])
    problems_by_region = iss.groupby("region").size().reindex(regions, fill_value=0).astype(int).to_dict()
    problems_by_type = iss.groupby("problem").size().sort_values(ascending=False).astype(int).to_dict()
    dup = df[df.nomenclature.notna()]
    dup_codes = int(dup.duplicated(["region", "kind", "nomenclature"], keep=False).sum())

    # ---- region board
    board = []
    for rg in regions:
        t = tx[tx.region == rg]
        ln_r = ln[ln.region == rg]
        board.append(dict(
            region=rg,
            tx_reporting=int((t.status == "Reporting").sum()), tx_total=len(t),
            mva_unavailable=float(mva_unavail.get(rg, 0)),
            tx_80=int(((loaded.region == rg) & (loaded.loading_pct >= 80)).sum()),
            n1_fail=sum(1 for d in n1_fail if d["region"] == rg),
            volt_exc=sum(exc_by_region[rg].values()),
            feeder_dip_pct=dip_share[rg],
            lines_energy_pct=_pct(ln_r.energy_reading.notna().sum(), len(ln_r)),
            data_problems=problems_by_region.get(rg, 0),
        ))

    facts = dict(
        month_label=month_label,
        regions=regions,
        counts=dict(transformers=len(tx), transformer_mva=float(tx.rating_mva.sum()), feeders=len(fd), lines=len(ln)),
        unavailable=dict(units=len(unavail), mva=float(unavail.rating_mva.sum()),
                         top_regions=[(rg, float(v)) for rg, v in mva_unavail.sort_values(ascending=False).head(3).items()]),
        loading=dict(n_with_loading=len(loaded), n_80=int((loaded.loading_pct >= 80).sum()),
                     n_over_100=int((loaded.loading_pct > 100).sum()),
                     n_under_40=int((loaded.loading_pct < 40).sum()),
                     suspect_entries=int(tx.loading_suspect.sum()),
                     top3=[f"{r.sub_key} ({r.region})" for r in top_loaded.head(3).itertuples()],
                     hottest=_hottest(loaded)),
        n1=dict(groups_multi=multi_count, fail=len(n1_fail), single=len(single),
                identical=[d["substation"] for d in n1_fail if d["identical_entries"]]),
        voltage=dict(excursions=len(excursions), by_region=exc_by_region,
                     worst_low_region=max(regions, key=lambda rg: exc_by_region[rg]["Low"]),
                     worst_high_region=max(regions, key=lambda rg: exc_by_region[rg]["High"])),
        boundary=dict(pairs=len(boundary), flagged=sum(1 for b in boundary if b["flag"])),
        feeders=dict(numeric_max=len(fnum), dip_total=dip_total, dip_pct=_pct(dip_total, len(fdip)),
                     dip_worst=max(regions, key=lambda rg: dip_share[rg]),
                     evening_pct=_pct(evening, sum(all_hours.values())),
                     busiest_hour=(all_hours.most_common(1)[0][0] if all_hours else None),
                     early_days=sum(day_grid["feeder"][:2]) if day_grid["feeder"] else 0),
        data=dict(total=len(issues), by_type=problems_by_type, shared_codes=dup_codes),
    )
    return dict(
        facts=facts, board=board, status_by_region=status_by_region, status_order=STATUS_ORDER,
        mva_unavail={rg: float(v) for rg, v in mva_unavail.items()},
        unavailable_rows=_records(unavail.sort_values("rating_mva", ascending=False),
                                  ["region", "substation", "name", "rating_mva", "status", "remarks"]),
        bands=bands, band_names=[b for b, _, _ in LOAD_BANDS],
        scatter=[dict(x=round(r.loading_pct, 1), y=r.hot_temp, label=f"{r.name} · {r.substation}")
                 for r in loaded[loaded.hot_temp.notna()].itertuples()],
        top_loaded=_records(top_loaded, ["region", "substation", "name", "rating_mva", "max_mw", "loading_pct", "hot_temp", "max_time", "max_date"]),
        n1_fail=n1_fail, single=single,
        top_flows=top_flows, v330=v330, excursions=excursions, exc_by_region=exc_by_region,
        boundary=boundary, hist=hist, dip_share=dip_share, hour_grid=hour_grid, day_grid=day_grid,
        meters=meters, problems_by_type=problems_by_type,
    )


def _amps_mismatch(mw, amps, kv) -> bool:
    """MW and amps that cannot both be right at the stated voltage (pf 0.8-1.0, 35% slack)."""
    if not all(v is not None and pd.notna(v) and v > 0 for v in (mw, amps, kv)):
        return False
    implied = 3 ** 0.5 * kv * amps / 1000
    return not (0.8 * implied * 0.65 <= mw <= implied * 1.35)


def _pct(a, b) -> int:
    return int(round(100 * a / b)) if b else 0


def _hottest(loaded: pd.DataFrame):
    h = loaded[loaded.hot_temp.notna()]
    if h.empty:
        return None
    r = h.loc[h.hot_temp.idxmax()]
    return dict(name=f"{r.sub_key} {r['name']}", temp=float(r.hot_temp), region=r.region)


def _records(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    out = []
    for r in df[cols].to_dict("records"):
        out.append({k: (None if (not isinstance(v, str) and v is not None and pd.isna(v)) else v) for k, v in r.items()})
    return out


def equipment_key(r: dict) -> str:
    """Stable id used to match the same unit from month to month.

    region | kind | substation (cleaned) | name (cleaned). Nomenclature is not used on its own
    because many rows share a code. Units that still fail to match go to manual review.
    """
    name = re.sub(r"[^A-Z0-9]", "", str(r.get("name") or "").upper())
    return f"{r['region']}|{r['kind']}|{substation_key(r.get('substation'))}|{name}"


def energy_consumption(prev_rows: list[dict], cur_rows: list[dict], hours_in_month: int,
                       multipliers: dict[str, float] | None = None) -> pd.DataFrame:
    """MWh used per meter = this month's closing reading - last month's closing reading (x multiplier).

    Flags anything that cannot be right instead of hiding it:
    missing reading, negative (meter reset or replaced), or more than max MW x hours in the month.
    """
    multipliers = multipliers or {}
    cur = pd.DataFrame(cur_rows)
    prev = pd.DataFrame(prev_rows)
    cur["equipment_id"] = [equipment_key(r) for r in cur_rows]
    prev["equipment_id"] = [equipment_key(r) for r in prev_rows]
    prev = prev.drop_duplicates("equipment_id")[["equipment_id", "energy_reading"]]
    m = cur[cur.kind.isin(["feeder", "transformer", "line"])].merge(prev, on="equipment_id", how="left", suffixes=("", "_prev"))
    m["multiplier"] = m.equipment_id.map(multipliers).fillna(1.0)
    m["mwh"] = (m.energy_reading - m.energy_reading_prev) * m.multiplier
    m["flag"] = None
    m.loc[m.energy_reading.isna() | m.energy_reading_prev.isna(), "flag"] = "missing reading"
    m.loc[m.mwh < 0, "flag"] = "negative: meter reset or replaced"
    m.loc[m.max_mw.notna() & (m.mwh > m.max_mw * hours_in_month), "flag"] = "more than max MW x hours in month"
    m.loc[m.flag.notna(), "mwh_ok"] = None
    m.loc[m.flag.isna(), "mwh_ok"] = m.mwh
    return m


def energy_summary(m: pd.DataFrame, price_per_kwh: dict[str, float] | None = None) -> dict:
    """Totals by region and kind from checked meters only. price_per_kwh: {equipment_id or region: naira/kWh}."""
    ok = m[m.mwh_ok.notna()].copy()
    if price_per_kwh:
        ok["price"] = ok.equipment_id.map(price_per_kwh).fillna(ok.region.map(price_per_kwh))
        ok["naira"] = (ok.mwh_ok * 1000 * ok.price).where(ok.kind == "feeder")
    by = ok.groupby(["region", "kind"]).agg(mwh=("mwh_ok", "sum"), meters=("mwh_ok", "size"))
    # Feeder meters only: transformer and line meters measure the same energy further upstream,
    # so adding all three would count it two or three times.
    return dict(total_mwh=float(ok[ok.kind == "feeder"].mwh_ok.sum()), checked=len(ok), flagged=int(m.flag.notna().sum()),
                flags=m.flag.value_counts().to_dict(), by_region_kind=by.reset_index().to_dict("records"),
                naira=float(ok.naira.sum()) if "naira" in ok else None)
