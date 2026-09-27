"""Build the monthly review: parse -> metrics -> words -> HTML -> PDF."""
from __future__ import annotations

import calendar
import datetime as dt
import hashlib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import svgcharts as sc
from .metrics import compute, POWER_FACTOR, energy_consumption, energy_summary
from .narrative import rule_based, with_llm
from .parser import parse_workbook

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
FONTS = Path(__file__).resolve().parent.parent / "fonts"


def font_css() -> str:
    """Barlow (SIL Open Font License) embedded so the report looks the same offline and in the PDF."""
    import base64
    out = []
    for fam, file_prefix in (("Barlow", "barlow-latin-"), ("Barlow Condensed", "barlow-condensed-latin-")):
        for w in (400, 500, 600, 700):
            p = FONTS / f"{file_prefix}{w}-normal.woff2"
            if p.exists():
                b64 = base64.b64encode(p.read_bytes()).decode()
                out.append(f"@font-face{{font-family:'{fam}';font-weight:{w};font-style:normal;font-display:swap;"
                           f"src:url(data:font/woff2;base64,{b64}) format('woff2')}}")
    return "\n".join(out)

SHORT = {"Port Harcourt": "P/Harcourt"}
STATUS_COLORS = {
    "Reporting": "#2e7d4f", "In service, no separate reading": "#94c3a3", "Energised / standby, no load": "#9fb6d3",
    "New / on soak / not connected": "#1f5fa8", "Maintenance / works": "#d9a93b", "Faulty / tripped": "#b3261e",
    "Out of service": "#b87a23", "Decommissioned / spare": "#7b8794", "No data": "#d3d9e1",
}
BAND_COLORS = {"under 40%": "#f3e2c7", "40–60%": "#edc08c", "60–80%": "#dc8e5c", "80–90%": "#b87a23", "90% and above": "#b3261e"}
REGION_COLORS = ["#b3261e", "#1f5fa8", "#2e7d4f", "#b87a23", "#7b4fb3", "#1a9aa0", "#d0621f", "#555f6e", "#b3306f", "#6f8f1f"]
KIND_COLORS = {"Feeders": "#2e7d4f", "Transformers": "#1f5fa8", "Lines": "#b3261e"}


def _num(v, d):
    if v is None:
        return ""
    try:
        return f"{float(v):,.{d}f}"
    except (TypeError, ValueError):
        return str(v)


def _board(board: list[dict], regions: list[str]):
    spec = [
        ("Transformers reporting / total", lambda b: b["tx_total"] - b["tx_reporting"], lambda b: f"{b['tx_reporting']} / {b['tx_total']}"),
        ("MVA unavailable", lambda b: b["mva_unavailable"], lambda b: f"{b['mva_unavailable']:,.0f}"),
        ("Transformers ≥ 80% loaded", lambda b: b["tx_80"], lambda b: str(b["tx_80"])),
        ("Substations failing N-1", lambda b: b["n1_fail"], lambda b: str(b["n1_fail"])),
        ("Line voltage excursions", lambda b: b["volt_exc"], lambda b: str(b["volt_exc"])),
        ("Feeders dipping to ≈0 MW", lambda b: b["feeder_dip_pct"], lambda b: f"{b['feeder_dip_pct']}%"),
        ("Lines with energy reading", lambda b: 100 - b["lines_energy_pct"], lambda b: f"{b['lines_energy_pct']}%"),
        ("Data corrections (cells)", lambda b: b["data_problems"], lambda b: f"{b['data_problems']:,}"),
    ]
    rows = []
    for label, severity, text in spec:
        vals = [severity(b) for b in board]
        top = max(vals) or 1
        rows.append(dict(label=label, cells=[dict(text=text(b), alpha=round(0.04 + 0.42 * (v / top), 2)) for b, v in zip(board, vals)]))
    return rows


def compute_report(files: list[tuple[str, Path]], year: int, month: int,
                    regions: list[str] | None = None, prev_rows: list[dict] | None = None,
                    multipliers: dict[str, float] | None = None,
                    price_per_kwh: dict[str, float] | None = None) -> dict:
    """files: list of (region or None, path). regions: limit the review to
    these regions; None = every region uploaded.

    Everything that only depends on the uploaded workbooks: parsing,
    metrics, charts, tables, energy. No AI call and no HTML here -- this
    is the half of the old build() that never needs to change once the
    files are read, so it's the piece cached across Streamlit reruns for
    the editable-text/chat features (write_text/render_html re-run cheaply
    against this without re-parsing anything)."""
    rows, issues, file_info = [], [], []
    for region, path in files:
        res = parse_workbook(path, year, month, region)
        rows += [r.as_dict() for r in res.rows]
        issues += res.issues
        file_info.append(dict(name=Path(path).name, region=res.region,
                              sha=hashlib.sha256(Path(path).read_bytes()).hexdigest()))
    month_name = calendar.month_name[month]
    month_label = f"{month_name} {year}"
    all_rows = rows
    if regions:
        wanted = set(regions)
        rows = [r for r in rows if r["region"] in wanted]
        issues = [i for i in issues if i["region"] in wanted]
        if not rows:
            raise ValueError(f"No rows found for {', '.join(regions)}")
    m = compute(rows, issues, month_label)
    if regions:
        # boundary lines need the region at the other end, so pair them using every uploaded region
        wanted = set(regions)
        m["boundary"] = [b for b in compute(all_rows, [], month_label)["boundary"]
                         if b["region_a"] in wanted or b["region_b"] in wanted]
        m["facts"]["boundary"] = dict(pairs=len(m["boundary"]), flagged=sum(1 for b in m["boundary"] if b["flag"]))
    f = m["facts"]
    n_reg = len(f["regions"])
    scope = (f"{f['regions'][0]} TCC" if n_reg == 1 else
             "the ten TCCs" if n_reg == 10 else f"{n_reg} TCCs")
    f["scope"] = scope
    energy_html, energy_df = None, None
    if prev_rows:
        hours = calendar.monthrange(year, month)[1] * 24
        energy_df = energy_consumption(prev_rows, rows, hours, multipliers)
        es = energy_summary(energy_df, price_per_kwh)
        f["energy"] = dict(total_mwh=round(es["total_mwh"]), meters_checked=es["checked"], meters_flagged=es["flagged"],
                           flags=es["flags"], naira=round(es["naira"]) if es["naira"] else None, hours=hours)
        energy_html = _energy_html(energy_df, es)
    regions = f["regions"]
    short = [SHORT.get(r, r) for r in regions]
    ctx = dict(board=m["board"], top_loaded=m["top_loaded"][:10], n1_fail=m["n1_fail"][:12],
               boundary=m["boundary"], excursions=m["excursions"][:25], top_flows=m["top_flows"][:10])

    # ---------------- charts
    ch, lg = {}, {}
    sbr = {SHORT.get(k, k): v for k, v in m["status_by_region"].items()}
    ch["status"] = sc.stacked_hbar(short, m["status_order"], STATUS_COLORS, sbr, "Transformers by status")
    lg["status"] = sc.legend([(s, STATUS_COLORS[s]) for s in m["status_order"]])
    ch["mva_out"] = sc.hbar(short, [m["mva_unavail"][r] for r in regions], "#b87a23", "MVA unavailable",
                            unit=" MVA", axis_label="MVA")
    bands = {SHORT.get(k, k): v for k, v in m["bands"].items()}
    ch["bands"] = sc.stacked_vbar(short, m["band_names"], BAND_COLORS, bands, "Peak loading band", y_label="Transformers")
    lg["bands"] = sc.legend([(b, BAND_COLORS[b]) for b in m["band_names"]])
    pts = []
    for p in m["scatter"]:
        col = "#b3261e" if p["x"] >= 80 else "#b87a23" if p["x"] >= 60 else "#1f5fa8"
        pts.append(dict(x=p["x"], y=p["y"], color=col, title=f"{p['label']}: {p['x']:.0f}% · {p['y']:.0f} °C"))
    ymax = max([p["y"] for p in pts] + [60])
    ch["scatter"] = sc.scatter(pts, "Loading against winding temperature", "Peak loading (%)", "Hottest winding at max (°C)",
                               (0, 100), (20, 10 * ((int(ymax) // 10) + 1)))
    lg["scatter"] = sc.legend([("under 60%", "#1f5fa8"), ("60–80%", "#b87a23"), ("80% and above", "#b3261e")])

    n1 = m["n1_fail"][:24]
    kvc = lambda ratio: "#b3261e" if ratio.startswith("330") else "#1f5fa8" if ratio.startswith("132") else "#2e7d4f"
    ch["n1"] = sc.paired_hbar([f"{d['substation']} {d['ratio']} ({SHORT.get(d['region'], d['region'])})" + (" ⚠" if d["identical_entries"] else "") for d in n1],
                              [d["firm_mw"] for d in n1], [d["peak_mw"] for d in n1], "#edc08c",
                              [kvc(d["ratio"]) for d in n1], "N-1 screen", "Firm capacity", "Sum of peaks")
    lg["n1"] = sc.legend([("Firm capacity (MW)", "#edc08c"), ("Sum of peaks, 330 kV", "#b3261e"), ("Sum of peaks, 132 kV", "#1f5fa8")]) + \
        ('<p class="note">⚠ units returned with identical figures: confirm before acting.</p>' if any(d["identical_entries"] for d in n1) else "")

    fl = m["top_flows"]
    ch["flows"] = sc.hbar([f"{d['line'][:40]} · {SHORT.get(d['region'], d['region'])}" + (" ⚠" if d["check"] else "") for d in fl],
                          [d["mw"] for d in fl], ["#b3261e"] * len(fl), "Highest line flows", unit=" MW", left=320, axis_label="MW")
    rc = {r: REGION_COLORS[i % len(REGION_COLORS)] for i, r in enumerate(regions)}
    vp = [dict(x=d["peak_kv"], y=d["min_kv"], color=rc[d["region"]], title=f"{d['line']} ({d['region']}): {d['peak_kv']:.0f} / {d['min_kv']:.0f} kV")
          for d in m["v330"]]
    ch["v330"] = sc.scatter(vp, "330 kV voltages", "kV at peak load", "kV at minimum load", (280, 370), (280, 370),
                            band=(313.5, 346.5, 313.5, 346.5))
    lg["regions"] = sc.legend([(SHORT.get(r, r), rc[r]) for r in regions])
    exc = {SHORT.get(k, k): v for k, v in m["exc_by_region"].items()}
    ch["exc"] = sc.stacked_vbar(short, ["High", "Low"], {"High": "#b3261e", "Low": "#1f5fa8"}, exc, "Voltage excursions", y_label="Readings")
    lg["exc"] = sc.legend([("High", "#b3261e"), ("Low", "#1f5fa8")])

    dg = m["day_grid"]
    days = {d + 1: {"Feeders": dg["feeder"][d] if d < len(dg["feeder"]) else 0,
                    "Transformers": dg["transformer"][d] if d < len(dg["transformer"]) else 0,
                    "Lines": dg["line"][d] if d < len(dg["line"]) else 0}
            for d in range(max(len(v) for v in dg.values()))}
    ch["days"] = sc.stacked_vbar(list(days), list(KIND_COLORS), KIND_COLORS, days, "Day of maximum", rotate=False, x_label=month_name)
    lg["days"] = sc.legend(list(KIND_COLORS.items()))
    ch["dip"] = sc.hbar(short, [m["dip_share"][r] for r in regions], "#b87a23", "Feeder minimum near zero", unit="%",
                        axis_label="% of feeders", vmax=100, show_values=True)
    hist = {h["label"]: {"Feeders": h["n"]} for h in m["hist"]}
    ch["hist"] = sc.stacked_vbar(list(hist), ["Feeders"], {"Feeders": "#2e7d4f"}, hist, "Feeder peak spread", y_label="Feeders", x_label="Peak MW")

    # heatmap rows
    hmax = max(max(v) for v in m["hour_grid"].values()) or 1
    hour_rows = [(SHORT.get(rg, rg), [dict(n=n, hour=f"{h:02d}", style=f"background:rgba(var(--heat),{0.06 + 0.6 * n / hmax:.2f})" if n else "")
                                      for h, n in enumerate(cells)]) for rg, cells in m["hour_grid"].items()]
    meter_rows = []
    for rg in regions:
        cells = []
        for k in ("feeder", "transformer", "line"):
            d = m["meters"][k][rg]
            cells.append(dict(n=d["with_reading"], t=d["total"], p=round(100 * d["with_reading"] / d["total"]) if d["total"] else 0))
        meter_rows.append(dict(region=rg, cells=cells))

    return dict(
        m=m, f=f, ctx=ctx, rows=rows, issues=issues, energy=energy_df, files=file_info,
        regions=regions, scope=scope, month_label=month_label, month_name=month_name,
        board_regions=short, board_rows=_board(m["board"], regions), hour_rows=hour_rows,
        meter_rows=meter_rows, energy_table=energy_html, charts=ch, legends=lg,
    )


def write_text(report_data: dict, api_key: str | None = None, model: str | None = None,
                provider: str = "anthropic") -> tuple[dict, list[str], str]:
    """The AI-or-rule-based paragraphs, unchanged in behaviour from the old
    build() -- just reading facts/ctx from a compute_report() result
    instead of local variables. provider: 'anthropic' or 'openai' (used
    only when api_key and model are given). Returns (text, notes,
    narrative_source) -- narrative_source is a 3rd value (the original
    module's spec only asked for 2) because render_html's template needs
    it and it shouldn't be re-derived by pattern-matching notes."""
    f, ctx = report_data["f"], report_data["ctx"]
    notes: list[str] = []
    if api_key and model:
        try:
            text, notes = with_llm(f, ctx, provider, api_key, model)
            who = "Claude" if provider == "anthropic" else "OpenAI"
            narrative_source = f"paragraphs written by {who} ({model}) from those figures and checked against them"
        except Exception as e:  # network, key, quota: the report still generates
            text = rule_based(f)
            narrative_source = "paragraphs generated from the figures by rule (the AI service was unavailable)"
            notes.append(f"AI call failed: {type(e).__name__}: {e}")
    else:
        text = rule_based(f)
        narrative_source = "paragraphs generated from the figures by rule"
    return text, notes, narrative_source


_ENV = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
_ENV.filters["num0"] = lambda v: _num(v, 0)
_ENV.filters["num1"] = lambda v: _num(v, 1)


def render_html(report_data: dict, text: dict, notes: list[str], narrative_source: str) -> str:
    """Pure and fast: the Jinja render only, no parsing and no AI call, so
    it's cheap to call again on every manual text edit or accepted chat
    proposal."""
    m, scope, month_label, month_name = report_data["m"], report_data["scope"], report_data["month_label"], report_data["month_name"]
    return _ENV.get_template("report.html.j2").render(
        scope=scope, font_css=font_css(), title=f"{month_label} load returns ({scope}) — Operations review", month_label=month_label, month_name=month_name,
        text=text, charts=report_data["charts"], legends=report_data["legends"], regions=report_data["regions"],
        board_regions=report_data["board_regions"], board_rows=report_data["board_rows"],
        unavailable_rows=m["unavailable_rows"], top_loaded=m["top_loaded"], single=m["single"], excursions=m["excursions"],
        boundary=m["boundary"], hour_rows=report_data["hour_rows"], meter_rows=report_data["meter_rows"],
        energy_table=report_data["energy_table"],
        problems=list(m["problems_by_type"].items()), pf=POWER_FACTOR, band330="313.5–346.5 kV",
        generated=dt.datetime.now().strftime("%d %b %Y, %H:%M"), narrative_source=narrative_source, notes=notes, files=report_data["files"],
    )


def build(files: list[tuple[str, Path]], year: int, month: int, api_key: str | None = None,
          model: str | None = None, prev_rows: list[dict] | None = None,
          price_per_kwh: dict[str, float] | None = None, multipliers: dict[str, float] | None = None,
          file_bytes: dict[str, bytes] | None = None, provider: str = "anthropic",
          regions: list[str] | None = None) -> dict:
    """Thin wrapper over compute_report/write_text/render_html, kept so
    every existing caller keeps working unchanged.
    Returns dict(html, metrics, rows, issues, notes, text, energy, files, scope)."""
    report_data = compute_report(files, year, month, regions=regions, prev_rows=prev_rows,
                                  multipliers=multipliers, price_per_kwh=price_per_kwh)
    text, notes, narrative_source = write_text(report_data, api_key=api_key, model=model, provider=provider)
    html = render_html(report_data, text, notes, narrative_source)
    return dict(html=html, metrics=report_data["m"], rows=report_data["rows"], issues=report_data["issues"],
                notes=notes, text=text, energy=report_data["energy"], files=report_data["files"], scope=report_data["scope"])


def _energy_html(df, es) -> str:
    from html import escape
    ok = df[df.mwh_ok.notna()]
    piv = ok.pivot_table(index="region", columns="kind", values="mwh_ok", aggfunc="sum", fill_value=0)
    flg = df[df.flag.notna()].groupby("region").size()
    rows = "".join(
        f"<tr><td>{escape(rg)}</td><td class='n'>{piv.loc[rg].get('feeder', 0):,.0f}</td>"
        f"<td class='n'>{piv.loc[rg].get('transformer', 0):,.0f}</td><td class='n'>{int(flg.get(rg, 0))}</td></tr>"
        for rg in piv.index)
    top = ok[ok.kind == "feeder"].sort_values("mwh_ok", ascending=False).head(15)
    trows = "".join(f"<tr><td>{escape(r.region)}</td><td>{escape(str(r.substation or ''))}</td><td>{escape(r.name)}</td>"
                    f"<td class='n'>{r.mwh_ok:,.0f}</td><td class='n'>{r.max_mw if r.max_mw == r.max_mw else '':}</td></tr>" for r in top.itertuples())
    bad = df[df.flag.notna() & (df.flag != "missing reading")].head(40)
    brows = "".join(f"<tr><td>{escape(r.region)}</td><td>{escape(r.name)}</td><td class='n'>{r.energy_reading_prev:,.1f}</td>"
                    f"<td class='n'>{r.energy_reading:,.1f}</td><td>{escape(r.flag)}</td></tr>" for r in bad.itertuples())
    naira = f"<p class='note'>Estimated value at the tariffs entered: ₦{es['naira']:,.0f}.</p>" if es.get("naira") else ""
    return (f"<table><thead><tr><th>Region</th><th class='n'>Feeders MWh</th><th class='n'>Transformers MWh</th><th class='n'>Meters flagged</th></tr></thead><tbody>{rows}</tbody></table>{naira}"
            f"<h3>Feeders that used the most energy</h3><table><thead><tr><th>Region</th><th>Substation</th><th>Feeder</th><th class='n'>MWh</th><th class='n'>Max MW</th></tr></thead><tbody>{trows}</tbody></table>"
            + (f"<h3>Meters to check before billing</h3><table><thead><tr><th>Region</th><th>Unit</th><th class='n'>Last month</th><th class='n'>This month</th><th>Problem</th></tr></thead><tbody>{brows}</tbody></table>" if brows else ""))


def html_to_pdf(html: str, out_path: str | Path | None = None) -> bytes:
    """Print the HTML with headless Chromium so the PDF keeps the same fonts, colours and charts."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.emulate_media(media="print", color_scheme="light")
        page.set_content(html, wait_until="networkidle")
        pdf = page.pdf(format="A4", print_background=True, prefer_css_page_size=True,
                       display_header_footer=True, header_template="<span></span>",
                       footer_template='<div style="font:9px Arial;color:#8791a0;width:100%;text-align:center">'
                                       '<span class="pageNumber"></span> / <span class="totalPages"></span></div>')
        browser.close()
    if out_path:
        Path(out_path).write_bytes(pdf)
    return pdf
