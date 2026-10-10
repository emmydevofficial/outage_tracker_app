"""
### FILE: tests/test_forecast_loss.py
Exact numeric worked examples from the forecast-loss spec (section 12),
run against utils/forecast_loss.py directly -- no database needed, since
the module is pure functions and these tests hand it synthetic
forecast_map dicts.
"""
import datetime as dt

import pandas as pd
import pytest

from utils.forecast_loss import (
    Slice, compute_outage, excess_forecast_loss, forecast_loss_mwh,
    gap_periods, load_method, price_slices, slice_outage, split_interval,
)


def _dt(*args):
    return dt.datetime(*args)


# ---------------------------------------------------------------------
# split_interval
# ---------------------------------------------------------------------

def test_split_interval_within_one_hour():
    assert split_interval(_dt(2026, 10, 5, 2, 10), _dt(2026, 10, 5, 2, 40)) == [(_dt(2026, 10, 5, 2, 0), 30.0)]


def test_split_interval_crosses_midnight():
    slices = split_interval(_dt(2026, 10, 5, 23, 40), _dt(2026, 10, 6, 0, 20))
    assert slices == [(_dt(2026, 10, 5, 23, 0), 20.0), (_dt(2026, 10, 6, 0, 0), 20.0)]


def test_split_interval_zarumai_example():
    slices = split_interval(_dt(2026, 10, 5, 2, 15), _dt(2026, 10, 5, 5, 17))
    assert slices == [
        (_dt(2026, 10, 5, 2, 0), 45.0), (_dt(2026, 10, 5, 3, 0), 60.0),
        (_dt(2026, 10, 5, 4, 0), 60.0), (_dt(2026, 10, 5, 5, 0), 17.0),
    ]


# ---------------------------------------------------------------------
# Section 12's worked examples
# ---------------------------------------------------------------------

def test_zarumai_forecast_loss_11_105_mwh():
    """ZARUMAI FDR, 2026-10-05 02:15 to 05:17, labels 01:00..06:00 = 4,4,4,3,3.9,4."""
    feeder_id = 1
    forecast_map = {
        (feeder_id, _dt(2026, 10, 5, 1, 0)): 4, (feeder_id, _dt(2026, 10, 5, 2, 0)): 4,
        (feeder_id, _dt(2026, 10, 5, 3, 0)): 4, (feeder_id, _dt(2026, 10, 5, 4, 0)): 3,
        (feeder_id, _dt(2026, 10, 5, 5, 0)): 3.9, (feeder_id, _dt(2026, 10, 5, 6, 0)): 4,
    }
    outage = dict(id=1, station="Minna 132kV", region="Shiroro", feeder_33kv="ZARUMAI FDR",
                  clipped_start=_dt(2026, 10, 5, 2, 15), clipped_end=_dt(2026, 10, 5, 5, 17), last_load=None)
    result = compute_outage(outage, feeder_id, forecast_map)
    assert result["forecast_loss_mwh"] == pytest.approx(11.105, abs=1e-6)
    assert result["load_method"] == "forecast"
    assert result["gap_periods"] == []


def test_midnight_crossing_uses_both_days_labels():
    feeder_id = 2
    forecast_map = {(feeder_id, _dt(2026, 10, 5, 23, 0)): 5.0, (feeder_id, _dt(2026, 10, 6, 0, 0)): 6.0}
    outage = dict(id=2, station="S", region="R", feeder_33kv="F",
                  clipped_start=_dt(2026, 10, 5, 23, 40), clipped_end=_dt(2026, 10, 6, 0, 20), last_load=None)
    result = compute_outage(outage, feeder_id, forecast_map)
    slices = result["slices"]
    assert [s.slot_start for s in slices] == [_dt(2026, 10, 5, 23, 0), _dt(2026, 10, 6, 0, 0)]
    assert result["forecast_loss_mwh"] == pytest.approx(5.0 * 20 / 60 + 6.0 * 20 / 60, abs=1e-4)


def test_missing_previous_day_workbook_gap_policies():
    """Outage 00:10-00:50 with no slot 00:00 stored -- a gap, "no workbook for that date"."""
    feeder_id = 3
    forecast_map: dict = {}  # nothing stored at all for this feeder/slot
    outage = dict(id=3, station="S", region="R", feeder_33kv="F",
                  clipped_start=_dt(2026, 10, 6, 0, 10), clipped_end=_dt(2026, 10, 6, 0, 50), last_load=5.0)

    unpriced = compute_outage(outage, feeder_id, forecast_map, gap_policy="unpriced")
    assert unpriced["forecast_loss_mwh"] == 0.0
    assert unpriced["unpriced_minutes"] == 40.0
    assert unpriced["load_method"] == "unpriced"
    assert unpriced["gap_periods"][0]["reason"] == "no workbook for that date"

    not_approved = compute_outage(outage, feeder_id, forecast_map, gap_policy="last_load", approved_slot_keys=set())
    assert not_approved["forecast_loss_mwh"] == 0.0
    assert not_approved["unpriced_minutes"] == 40.0

    approved_keys = {(feeder_id, _dt(2026, 10, 6, 0, 0))}
    approved = compute_outage(outage, feeder_id, forecast_map, gap_policy="last_load", approved_slot_keys=approved_keys)
    assert approved["forecast_loss_mwh"] == pytest.approx(5.0 * 40 / 60, abs=1e-4)
    assert approved["slices"][0].source == "last_load (approved)"
    assert approved["load_method"] == "forecast_with_last_load"


def test_approved_gap_with_no_last_load_stays_unpriced_with_specific_reason():
    feeder_id = 4
    forecast_map: dict = {}
    outage = dict(id=4, station="S", region="R", feeder_33kv="F",
                  clipped_start=_dt(2026, 10, 6, 0, 10), clipped_end=_dt(2026, 10, 6, 0, 50), last_load=None)
    approved_keys = {(feeder_id, _dt(2026, 10, 6, 0, 0))}
    result = compute_outage(outage, feeder_id, forecast_map, gap_policy="last_load", approved_slot_keys=approved_keys)
    assert result["forecast_loss_mwh"] == 0.0
    assert result["slices"][0].source == "unpriced"
    assert result["slices"][0].gap_reason == "no forecast and no last_load"


def test_partial_coverage_gives_one_120_minute_gap_period():
    """2026-10-04 22:00 to 2026-10-05 03:00: Oct-4 workbook exists (so
    22:00, 23:00 and Oct-4's "24:00" -> Oct-5 00:00 are priced), Oct-5's
    own workbook does not (01:00, 02:00 are a gap)."""
    feeder_id = 5
    forecast_map = {
        (feeder_id, _dt(2026, 10, 4, 22, 0)): 2.0,
        (feeder_id, _dt(2026, 10, 4, 23, 0)): 2.0,
        (feeder_id, _dt(2026, 10, 5, 0, 0)): 2.0,  # Oct-4 workbook's "24:00" label
    }
    outage = dict(id=5, station="S", region="R", feeder_33kv="F",
                  clipped_start=_dt(2026, 10, 4, 22, 0), clipped_end=_dt(2026, 10, 5, 3, 0), last_load=None)
    result = compute_outage(outage, feeder_id, forecast_map)
    assert result["forecast_loss_mwh"] == pytest.approx(6.0, abs=1e-6)  # 3 priced hours x 2.0 MW
    assert result["unpriced_minutes"] == 120.0
    assert result["load_method"] == "partly_unpriced"
    gaps = result["gap_periods"]
    assert len(gaps) == 1
    assert gaps[0]["gap_start"] == _dt(2026, 10, 5, 1, 0)
    assert gaps[0]["gap_end"] == _dt(2026, 10, 5, 3, 0)
    assert gaps[0]["minutes"] == 120.0
    assert gaps[0]["reason"] == "no workbook for that date"


def test_feeder_not_matched_is_its_own_gap_reason():
    outage = dict(id=6, station="S", region="R", feeder_33kv="F",
                  clipped_start=_dt(2026, 10, 6, 1, 0), clipped_end=_dt(2026, 10, 6, 2, 0), last_load=None)
    result = compute_outage(outage, None, {})
    assert result["gap_periods"][0]["reason"] == "feeder not matched"
    assert result["load_method"] == "unpriced"


def test_blank_forecast_cell_vs_no_workbook_distinct_reasons():
    feeder_id = 7
    forecast_map = {(feeder_id, _dt(2026, 10, 6, 1, 0)): None}  # row exists, cell was blank/invalid (R30/R31)
    outage = dict(id=7, station="S", region="R", feeder_33kv="F",
                  clipped_start=_dt(2026, 10, 6, 1, 0), clipped_end=_dt(2026, 10, 6, 2, 0), last_load=None)
    result = compute_outage(outage, feeder_id, forecast_map)
    assert result["gap_periods"][0]["reason"] == "blank forecast cell"


# ---------------------------------------------------------------------
# load_method coverage
# ---------------------------------------------------------------------

def test_load_method_all_sources():
    base = dict(slot_start=_dt(2026, 1, 1, 0, 0), minutes=60.0, forecast_mw=1.0, is_gap=False, gap_reason=None)
    forecast_slice = Slice(**base, mwh=1.0, source="forecast")
    last_load_slice = Slice(**{**base, "is_gap": True}, mwh=1.0, source="last_load (approved)")
    unpriced_slice = Slice(**{**base, "is_gap": True}, mwh=0.0, source="unpriced")

    assert load_method([forecast_slice]) == "forecast"
    assert load_method([forecast_slice, last_load_slice]) == "forecast_with_last_load"
    assert load_method([forecast_slice, unpriced_slice]) == "partly_unpriced"
    assert load_method([unpriced_slice]) == "unpriced"


# ---------------------------------------------------------------------
# excess_forecast_loss -- allocation exhaustion mid-outage, and an
# outage entirely after the allocation is already used up
# ---------------------------------------------------------------------

def test_excess_forecast_loss_splits_mid_outage_and_covers_later_outages_whole():
    feeder_id = 9
    key = ("S", "F")
    forecast_map = {
        (feeder_id, _dt(2026, 1, 1, 1, 0)): 6.0,  # covers the excess tail of outage B
        (feeder_id, _dt(2026, 1, 1, 3, 0)): 8.0,  # covers the whole of outage C
    }
    df_tcn = pd.DataFrame([
        dict(id=10, station="S", feeder_33kv="F", clipped_start=_dt(2026, 1, 1, 0, 0),
             clipped_end=_dt(2026, 1, 1, 0, 30), duration_hr=0.5, last_load=3.0),
        dict(id=11, station="S", feeder_33kv="F", clipped_start=_dt(2026, 1, 1, 1, 0),
             clipped_end=_dt(2026, 1, 1, 2, 0), duration_hr=1.0, last_load=3.0),
        dict(id=12, station="S", feeder_33kv="F", clipped_start=_dt(2026, 1, 1, 3, 0),
             clipped_end=_dt(2026, 1, 1, 3, 15), duration_hr=0.25, last_load=3.0),
    ])
    out = excess_forecast_loss(df_tcn, {key: 1.0}, {key: feeder_id}, forecast_map)
    assert len(out) == 2  # outage A (10) fits fully inside the 1.0hr allocation -- no excess

    b = next(o for o in out if o["outage_id"] == 11)
    assert b["excess_start"] == _dt(2026, 1, 1, 1, 30)
    assert b["excess_hrs"] == pytest.approx(0.5, abs=1e-6)
    assert b["excess_forecast_mwh"] == pytest.approx(3.0, abs=1e-6)  # 6.0 MW x 30/60

    c = next(o for o in out if o["outage_id"] == 12)
    assert c["excess_start"] == _dt(2026, 1, 1, 3, 0)  # entirely excess, nothing clipped off the front
    assert c["excess_hrs"] == pytest.approx(0.25, abs=1e-6)
    assert c["excess_forecast_mwh"] == pytest.approx(2.0, abs=1e-6)  # 8.0 MW x 15/60
