"""
### FILE: utils/forecast_loss.py
Turns an outage's clipped interval into a forecast-based MWh figure using
feeder_hourly_forecast instead of a flat last_load x duration assumption.
Pure functions only -- no Streamlit, no database reads. The caller (the
Reliability KPI (Forecast Loss) page) loads outages/forecast rows once
and passes them in as plain DataFrames/dicts.

A gap (no usable forecast for a slot) is NEVER filled silently. Every
pricing function takes gap_policy ("unpriced" or "last_load") and, for
"last_load", an explicit approved_slot_keys set the caller only ever
builds from a user's own confirmed approval -- see spec section 8/9.
"""
from __future__ import annotations

import calendar
import dataclasses
import datetime as dt
from dataclasses import dataclass, field

GAP_POLICIES = ("unpriced", "last_load")


@dataclass
class Slice:
    slot_start: dt.datetime
    minutes: float
    forecast_mw: float | None
    is_gap: bool
    gap_reason: str | None
    mwh: float = 0.0
    source: str = "unpriced"  # "forecast" | "last_load (approved)" | "unpriced"


def split_interval(start_ts: dt.datetime, end_ts: dt.datetime) -> list[tuple[dt.datetime, float]]:
    """[(slot_start, minutes), ...] -- cuts start_ts..end_ts on every clock
    hour. slot_start is the hour the slice belongs to (e.g. the 02:15-02:45
    portion of an outage belongs to slot_start=02:00). Handles outages that
    cross midnight or span several days by just continuing to the next
    hour boundary regardless of the calendar date."""
    if end_ts <= start_ts:
        return []
    out = []
    cur = start_ts
    while cur < end_ts:
        slot_start = cur.replace(minute=0, second=0, microsecond=0)
        next_hour = slot_start + dt.timedelta(hours=1)
        slice_end = min(end_ts, next_hour)
        minutes = (slice_end - cur).total_seconds() / 60.0
        out.append((slot_start, minutes))
        cur = slice_end
    return out


def month_clip_bounds(start_date: dt.date, end_date: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """The calendar-month-aligned clip window for a selected date range --
    same rule as views/reliability_kpi_report.py's month_start/month_end
    (the 1st of start_date's month through the end of end_date's month),
    NOT reliability_report.data.read_outages()'s plain period clip. A
    cross-month outage should only count the hours inside the viewed
    month(s), same as the existing page."""
    month_start = dt.datetime(start_date.year, start_date.month, 1)
    last_day = calendar.monthrange(end_date.year, end_date.month)[1]
    month_end = dt.datetime(end_date.year, end_date.month, last_day) + dt.timedelta(days=1)
    return month_start, month_end


def _gap_reason(feeder_id, key, forecast_map: dict) -> str:
    if feeder_id is None:
        return "feeder not matched"
    if key not in forecast_map:
        return "no workbook for that date"
    return "blank forecast cell"


def slice_outage(feeder_id: int | None, clipped_start: dt.datetime, clipped_end: dt.datetime,
                  forecast_map: dict[tuple[int, dt.datetime], float | None]) -> list[Slice]:
    """Raw hourly slices for one outage's clipped interval, with gaps
    identified but NOT yet priced -- the same raw slices feed both the
    gap-review table (section 9) and the final priced total, so pricing
    (gap_policy) is applied afterwards by price_slices()."""
    slices = []
    for slot_start, minutes in split_interval(clipped_start, clipped_end):
        key = (feeder_id, slot_start)
        forecast_mw = forecast_map.get(key) if feeder_id is not None else None
        is_gap = feeder_id is None or key not in forecast_map or forecast_mw is None
        reason = _gap_reason(feeder_id, key, forecast_map) if is_gap else None
        slices.append(Slice(slot_start=slot_start, minutes=minutes, forecast_mw=forecast_mw,
                             is_gap=is_gap, gap_reason=reason))
    return slices


def gap_periods(outage_row: dict, raw_slices: list[Slice]) -> list[dict]:
    """Merge consecutive gap slices of one outage into gap periods for the
    review table: feeder, station, region, outage id, gap_start, gap_end,
    minutes, reason, last_load. A reason change (e.g. "no workbook for
    that date" into "blank forecast cell") starts a new period even if
    the slices are back-to-back in time."""
    periods: list[dict] = []
    cur: dict | None = None
    for s in raw_slices:
        if not s.is_gap:
            if cur:
                periods.append(cur)
                cur = None
            continue
        if cur and cur["reason"] == s.gap_reason and cur["gap_end"] == s.slot_start:
            cur["gap_end"] = s.slot_start + dt.timedelta(minutes=s.minutes)
            cur["minutes"] += s.minutes
        else:
            if cur:
                periods.append(cur)
            cur = dict(
                outage_id=outage_row.get("id"), feeder_id=outage_row.get("feeder_id"),
                station=outage_row.get("station"), region=outage_row.get("region"),
                feeder=outage_row.get("feeder_33kv") or outage_row.get("feeder"),
                gap_start=s.slot_start, gap_end=s.slot_start + dt.timedelta(minutes=s.minutes),
                minutes=s.minutes, reason=s.gap_reason, last_load=outage_row.get("last_load"),
            )
    if cur:
        periods.append(cur)
    for p in periods:
        p["minutes"] = round(p["minutes"], 2)
    return periods


def price_slices(raw_slices: list[Slice], last_load: float | None, feeder_id: int | None,
                  gap_policy: str = "unpriced", approved_slot_keys: set[tuple] | None = None) -> list[Slice]:
    """Fills in mwh/source for every slice, honoring gap_policy. A gap only
    ever gets last_load when gap_policy=="last_load" AND its (feeder_id,
    slot_start) key is in approved_slot_keys -- a set the caller builds
    only from gap periods the user actually confirmed on the page, never
    silently."""
    if gap_policy not in GAP_POLICIES:
        raise ValueError(f"unknown gap_policy: {gap_policy!r}")
    approved_slot_keys = approved_slot_keys or set()
    priced = []
    for s in raw_slices:
        if not s.is_gap:
            mwh = s.forecast_mw * s.minutes / 60.0
            priced.append(dataclasses.replace(s, mwh=mwh, source="forecast"))
            continue
        approved = gap_policy == "last_load" and (feeder_id, s.slot_start) in approved_slot_keys
        if approved and last_load is not None:
            mwh = last_load * s.minutes / 60.0
            priced.append(dataclasses.replace(s, mwh=mwh, source="last_load (approved)"))
        elif approved and last_load is None:
            priced.append(dataclasses.replace(s, mwh=0.0, source="unpriced",
                                              gap_reason="no forecast and no last_load"))
        else:
            priced.append(dataclasses.replace(s, mwh=0.0, source="unpriced"))
    return priced


def load_method(priced_slices: list[Slice]) -> str:
    """'forecast' (no gaps) | 'forecast_with_last_load' (some gaps filled
    after approval, none left unpriced) | 'partly_unpriced' (a mix) |
    'unpriced' (nothing priced at all)."""
    sources = {s.source for s in priced_slices}
    if not sources or sources == {"forecast"}:
        return "forecast"
    if "unpriced" in sources:
        return "partly_unpriced" if sources - {"unpriced"} else "unpriced"
    return "forecast_with_last_load"


def forecast_loss_mwh(priced_slices: list[Slice]) -> float:
    return round(sum(s.mwh for s in priced_slices), 4)


def unpriced_minutes(priced_slices: list[Slice]) -> float:
    return round(sum(s.minutes for s in priced_slices if s.source == "unpriced"), 2)


def compute_outage(outage_row: dict, feeder_id: int | None, forecast_map: dict,
                    gap_policy: str = "unpriced", approved_slot_keys: set[tuple] | None = None) -> dict:
    """One-call orchestration for a single outage: outage_row needs id,
    station, region, feeder_33kv (or feeder), clipped_start, clipped_end,
    last_load. Returns slices (priced), forecast_loss_mwh, unpriced_minutes,
    load_method, and gap_periods (computed from the raw, policy-independent
    slices, so the review table is the same regardless of gap_policy)."""
    raw = slice_outage(feeder_id, outage_row["clipped_start"], outage_row["clipped_end"], forecast_map)
    last_load = outage_row.get("last_load")
    priced = price_slices(raw, last_load, feeder_id, gap_policy, approved_slot_keys)
    return dict(
        slices=priced,
        forecast_loss_mwh=forecast_loss_mwh(priced),
        unpriced_minutes=unpriced_minutes(priced),
        load_method=load_method(priced),
        gap_periods=gap_periods(outage_row, raw),
    )


def excess_forecast_loss(df_tcn, allocation_hrs_by_feeder: dict[tuple[str, str], float],
                          feeder_id_by_feeder: dict[tuple[str, str], int | None], forecast_map: dict,
                          gap_policy: str = "unpriced", approved_slot_keys: set[tuple] | None = None) -> list[dict]:
    """Same time-ordered allocation-exhaustion rule as
    reliability_report.metrics.exceedance()'s _excess_mwh: a feeder's TCN
    outages use up its allocation in the order they happened, and only
    time after the allocation runs out is excess. Unlike the existing
    exceedance logic (excess_hours x last_load), the excess TAIL of the
    outage where the allocation runs out -- and the whole interval of
    every outage after it -- is sliced against the forecast and priced
    the same way as compute_outage(), so the excess cost uses the same
    forecast data as the headline loss figure.

    df_tcn: TCN-only, closed, time-ordered-by-caller outage rows (same
    shape as reliability_report.data.read_outages()'s output) with
    clipped_start/clipped_end already computed by the caller (month clip,
    section 8). allocation_hrs_by_feeder / feeder_id_by_feeder are keyed
    by (station, feeder_33kv), e.g. from
    reliability_report.metrics.sla_compliance() and the outages-source
    alias resolution respectively.

    Returns one dict per outage that has any excess at all: station,
    feeder, outage_id, excess_start, excess_end, excess_hrs,
    excess_forecast_mwh, slices (priced, for the per-outage expander).
    """
    approved_slot_keys = approved_slot_keys or set()
    events_by_key: dict[tuple, list] = {}
    for r in df_tcn.sort_values(["clipped_start", "id"]).itertuples():
        events_by_key.setdefault((r.station, r.feeder_33kv), []).append(r)

    out = []
    for key, allocation_hrs in allocation_hrs_by_feeder.items():
        events = events_by_key.get(key, [])
        feeder_id = feeder_id_by_feeder.get(key)
        running = 0.0
        for ev in events:
            hours = 0.0 if _isna(ev.duration_hr) else float(ev.duration_hr)
            new_running = running + hours
            if new_running <= allocation_hrs:
                running = new_running
                continue
            remaining = max(allocation_hrs - running, 0.0)
            excess_start = min(ev.clipped_start + dt.timedelta(hours=remaining), ev.clipped_end)
            raw = slice_outage(feeder_id, excess_start, ev.clipped_end, forecast_map)
            last_load = None if _isna(ev.last_load) else float(ev.last_load)
            priced = price_slices(raw, last_load, feeder_id, gap_policy, approved_slot_keys)
            out.append(dict(
                station=key[0], feeder=key[1], outage_id=ev.id,
                excess_start=excess_start, excess_end=ev.clipped_end,
                excess_hrs=round((ev.clipped_end - excess_start).total_seconds() / 3600.0, 2),
                excess_forecast_mwh=forecast_loss_mwh(priced),
                slices=priced,
            ))
            running = new_running
    return out


def _isna(v) -> bool:
    """Avoids importing pandas just for pd.isna() -- NaN is the only
    float that isn't equal to itself; None is the other missing marker
    this module's callers (DataFrame .itertuples()) can hand us."""
    return v is None or (isinstance(v, float) and v != v)


def cost_ngn(mwh: float, rate_ngn_per_kwh: float) -> float:
    return round(mwh * 1000 * rate_ngn_per_kwh, 2)
