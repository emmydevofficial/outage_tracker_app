"""Naira/kWh tariff used to estimate the cost of TCN's outage-hour exceedance.

Rates are keyed by (disco, band) -- see utils/db.py's tariff_rates /
tariff_settings tables, editable via pages/20_Tariff_Settings.py (Super
Admin only). A feeder with no band is treated as Band A. A feeder with no
disco, or a (disco, band) combination with no rate configured yet, falls
back to the single global default rate.
"""
from utils.db import read_tariff_settings, read_tariff_rates

DEFAULT_BAND = "A"
_FALLBACK_RATE_NGN_PER_KWH = 206.50  # only used if tariff_settings has no row at all


def get_tariff_rate(disco=None, band=None, rates_df=None, default_rate=None) -> float:
    """Naira per kWh for a given disco+band.

    rates_df/default_rate can be passed in (already fetched once per page
    load) to avoid a DB round trip per row; if omitted they're fetched here.
    """
    if rates_df is None:
        rates_df = read_tariff_rates()
    if default_rate is None:
        default_rate = read_tariff_settings()

    band = (str(band).strip().upper() if band and str(band).strip() else "") or DEFAULT_BAND
    if disco and not rates_df.empty:
        disco_key = str(disco).strip().upper()
        match = rates_df[
            (rates_df["disco"].astype(str).str.strip().str.upper() == disco_key)
            & (rates_df["band"].astype(str).str.strip().str.upper() == band)
        ]
        if not match.empty:
            return float(match.iloc[0]["rate_ngn_per_kwh"])

    return float(default_rate) if default_rate is not None else _FALLBACK_RATE_NGN_PER_KWH
