"""One-off seed: builds the feeder identity registry from the two
sources that currently know about feeders -- tcn_sla_compliance (the
national SLA table) and the sample daily workbook -- and links both
with aliases. A later real upload (phase 2) adds daily_workbook
aliases for whatever it doesn't already recognize; the outages table
gets its own 'outages'-source aliases lazily, at query time, not here.

Matching key: (normalize_station(station), normalize_feeder(feeder)) --
reliability_report.data's proven normalization (0/O digit-letter fix,
voltage-token stripping), not reimplemented here.

Not idempotent -- like reliability_report/seed_issued_weeks.py, this is
meant to run once. Re-running it will hit feeder_alias's own UNIQUE
constraint rather than silently duplicate anything, so it's safe in the
sense that it fails loudly rather than corrupting data, but it isn't
designed to be run twice.

Run from outage_tracker-main/: ./venv/bin/python feeder_forecast/seed_feeders.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
from sqlalchemy import column, insert, table, text

from reliability_report.data import normalize_feeder, normalize_station
from utils.db import get_engine

SAMPLE_WORKBOOK = Path(__file__).resolve().parent.parent / "Claude outputs" / "SHIRORO_05102026.xlsx"


def load_sla_feeders(engine) -> list[dict]:
    with engine.connect() as con:
        rows = con.execute(text(
            "SELECT DISTINCT region, disco, station, feeder_name, feeder_band FROM tcn_sla_compliance"
        )).fetchall()
    return [dict(r._mapping) for r in rows]


def load_workbook_feeders(path: Path) -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True)
    region = str(wb["Control"]["L3"].value or "").strip()
    ws = wb["Record and Accounting"]
    out = []
    for r in range(4, ws.max_row + 1):
        col_a = str(ws.cell(r, 1).value or "").strip().upper()
        if col_a == "TOTAL":
            break
        station = ws.cell(r, 4).value
        if station is None or not str(station).strip():
            continue
        feeder = ws.cell(r, 8).value
        if feeder is None or not str(feeder).strip():
            continue
        out.append(dict(
            region=region,
            disco=str(ws.cell(r, 2).value or "").strip() or None,
            area_control=str(ws.cell(r, 3).value or "").strip() or None,
            station=str(station).strip(),
            transformer=str(ws.cell(r, 5).value or "").strip() or None,
            rating=str(ws.cell(r, 6).value).strip() if ws.cell(r, 6).value is not None else None,
            feeder_band=str(ws.cell(r, 7).value).strip() or None if ws.cell(r, 7).value is not None else None,
            feeder_name=str(feeder).strip(),
        ))
    return out


def main():
    engine = get_engine()
    sla_rows = load_sla_feeders(engine)
    wb_rows = load_workbook_feeders(SAMPLE_WORKBOOK)
    print(f"SLA rows read: {len(sla_rows)}  |  workbook rows read: {len(wb_rows)} (region: {wb_rows[0]['region'] if wb_rows else '?'})")

    # key -> {"sla": row|None, "workbook": row|None}
    registry: dict[tuple[str, str], dict] = {}
    for r in sla_rows:
        key = (normalize_station(r["station"]), normalize_feeder(r["feeder_name"]))
        registry.setdefault(key, {})["sla"] = r
    for r in wb_rows:
        key = (normalize_station(r["station"]), normalize_feeder(r["feeder_name"]))
        registry.setdefault(key, {})["workbook"] = r

    sla_only = sum(1 for v in registry.values() if "sla" in v and "workbook" not in v)
    wb_only = sum(1 for v in registry.values() if "workbook" in v and "sla" not in v)
    both = sum(1 for v in registry.values() if "sla" in v and "workbook" in v)
    print(f"Unique (station, feeder) keys: {len(registry)}  |  SLA-only: {sla_only}  |  workbook-only: {wb_only}  |  matched both: {both}")

    # Batched inserts (SQLAlchemy Core insert(), not one text() INSERT per
    # row): ~920 feeders x up to 2 round trips each, held open in one
    # transaction over a remote, sometimes-high-latency connection, is
    # exactly the pattern that already caused a multi-minute hang earlier
    # this session (monthly_report/store.py) -- and did again here on the
    # first attempt, which sat "idle in transaction" for 10+ minutes
    # waiting on a stalled network read and had to be killed. Batching
    # turns ~2,700 round trips into two.
    keys = list(registry.items())
    feeder_rows = []
    for (station_norm, feeder_norm), sources in keys:
        sla = sources.get("sla")
        wbk = sources.get("workbook")
        # prefer the workbook's fields when both exist -- it carries more
        # (area_control, transformer, rating, disco) than the SLA table.
        primary = wbk or sla
        feeder_rows.append(dict(
            region=primary["region"],
            disco=(wbk or {}).get("disco") or (sla or {}).get("disco"),
            area_control=(wbk or {}).get("area_control"),
            station=primary["station"],
            station_norm=station_norm,
            transformer=(wbk or {}).get("transformer"),
            rating=(wbk or {}).get("rating"),
            feeder_band=(wbk or {}).get("feeder_band") or (sla or {}).get("feeder_band"),
            canonical_name=(wbk or sla)["feeder_name"],
            created_by="seed_feeders", updated_by="seed_feeders",
        ))

    feeder_tbl = table("feeder", *(column(c) for c in
        ["feeder_id", "region", "disco", "area_control", "station", "station_norm", "transformer",
         "rating", "feeder_band", "canonical_name", "created_by", "updated_by"]))
    alias_tbl = table("feeder_alias", *(column(c) for c in
        ["feeder_id", "source", "station_norm", "name_raw", "name_norm", "created_by"]))

    with engine.begin() as con:
        result = con.execute(insert(feeder_tbl).returning(feeder_tbl.c.feeder_id), feeder_rows)
        feeder_ids = [row[0] for row in result.fetchall()]

        alias_rows = []
        for ((station_norm, feeder_norm), sources), feeder_id in zip(keys, feeder_ids):
            sla, wbk = sources.get("sla"), sources.get("workbook")
            if sla:
                alias_rows.append(dict(feeder_id=feeder_id, source="sla", station_norm=station_norm,
                                       name_raw=sla["feeder_name"], name_norm=feeder_norm, created_by="seed_feeders"))
            if wbk:
                alias_rows.append(dict(feeder_id=feeder_id, source="daily_workbook", station_norm=station_norm,
                                       name_raw=wbk["feeder_name"], name_norm=feeder_norm, created_by="seed_feeders"))
        con.execute(insert(alias_tbl), alias_rows)

    created = len(feeder_ids)
    print(f"Created {created} feeder row(s), {len(alias_rows)} alias row(s).")

    print("\n--- Shiroro workbook feeders (all 59, by resolution) ---")
    for r in wb_rows:
        key = (normalize_station(r["station"]), normalize_feeder(r["feeder_name"]))
        matched_sla = "sla" in registry[key]
        tag = "matched SLA" if matched_sla else "workbook-only"
        flag = ""
        if r["feeder_name"].upper() in ("IBETO", "CCNN") or not r["feeder_band"]:
            flag = " [blank band]" if not r["feeder_band"] else ""
        print(f"  {r['station']:20} / {r['feeder_name']:20} -> {tag}{flag}")


if __name__ == "__main__":
    main()
