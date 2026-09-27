"""One-off seed: Weeks 1 and 2 of September 2026 as ISSUED snapshots, read
directly from the real Management Summary's own tables 6 and 7 (not
recalculated -- these are historical, already-issued figures the spec
says to seed verbatim). Safe to re-run: it always inserts a fresh row, so
only run this once; check rel_report_run first if unsure.

Run from outage_tracker-main/: ./venv/bin/python reliability_report/seed_issued_weeks.py
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reliability_report import store
from reliability_report.periods import week_periods
from utils.db import get_engine

PER_REGION_COST = {
    "Abuja": (254195305.67, 0.00), "Bauchi": (0.00, 0.00), "Benin": (2526183.33, 0.00),
    "Enugu": (26911768.33, 335110953.33), "Kaduna": (0.00, 8948.33), "Kano": (0.00, 0.00),
    "Lagos": (19898787.42, 47802685.00), "Osogbo": (0.00, 30797065.83),
    "Port Harcourt": (11795280.00, 11567785.84), "Shiroro": (0.00, 88559590.00),
}

WEEK_FACTS = {
    1: dict(outages=487, hours=1033.87, load_loss_mwh=6308.97, feeders_over=25,
            excess_hrs=182.0, excess_load_loss_mwh=1527.01, estimated_cost_ngn=315327324.08),
    2: dict(outages=1348, hours=3304.10, load_loss_mwh=14607.98, feeders_over=39,
            excess_hrs=738.3, excess_load_loss_mwh=2488.38, estimated_cost_ngn=513847028.33),
}


def main():
    engine = get_engine()
    weeks = week_periods(2026, 9)
    for week_num in (1, 2):
        period = weeks[week_num - 1]
        facts = dict(WEEK_FACTS[week_num])
        facts["cost_by_region"] = {r: costs[week_num - 1] for r, costs in PER_REGION_COST.items()}
        facts["seeded_from"] = "issued Word report (Management Summary, tables 6 and 5.1/7)"
        run_id = store.save_run(
            engine, period_type="week", period_start=period.start, period_end=period.end,
            period_label=period.label, scope="ALL", facts=facts, ai_text={}, notes=[],
            model="seeded", user="seed_script", docx=None,
        )
        store.mark_issued(engine, [run_id], user="seed_script")
        print(f"Seeded {period.label} ({period.start}..{period.end}) as run id {run_id}, marked issued.")


if __name__ == "__main__":
    main()
