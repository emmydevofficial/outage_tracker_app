"""
Shared helpers for parsing the hourly load-tracking Excel sheets (33kV feeder,
line, and transformer load) into upload-ready rows. Column positions differ
per sheet type, so callers pass in which columns map to what; the merged-cell
forward-fill, hour-header normalization, and fault-code classification logic
is identical across all three.
"""

import re
from datetime import time as dt_time

# Cause codes that can appear in a load cell instead of a number, keyed by the
# exact short code used in the source sheets (case/whitespace-insensitive).
FAULT_CODES = {
    "CB/F": "CB FAULT",
    "132KV L/F": "132kV LINE FAULT",
    "330KV L/F": "330kV LINE FAULT",
    "T/F": "TRANSFORMER FAULT",
    "MTCE T": "MAINTENANCE WORK BY TRANSMISSION",
    "MTCE D": "MAINTENANCE WORK BY DISCO",
    "MTCE E.C.": "MAINTENANCE WORK BY ELIGIBLE CUSTOMER",
    "T/L": "LOAD SHEDDING DUE TO TRANSFORMER LIMITATION",
    "L/L": "LOAD SHEDDING DUE TO LINE LIMITATIONS",
    "L/S G/S": "LOAD SHEDDING DUE TO GENERATION",
    "U/F": "UNDER FREQUENCY",
    "E/F": "LINE TRIPPED, EARTH FAULT RELAY FLAGGED",
    "O/C": "LINE TRIPPED, OVER CURRENT RELAY FLAGGED",
    "NR": "LINE TRIPPED, NO RELAY FLAGGED",
    "EMRG. T": "THE LINE WAS OPENED DUE TO EMERGENCY SITUATIONS AT TRANSMISSION END",
    "EMRG. D": "THE LINE WAS OPENED DUE TO EMERGENCY SITUATIONS AT DISCO END",
    "EMRG. E.C.": "THE LINE WAS OPENED DUE TO EMERGENCY SITUATIONS AT ELIGIBLE CUSTOMER END",
    "SYS CLPS": "SYSTEM COLLAPSE",
    "O/V": "OVER VOLTAGE",
    "INST E/F": "INSTANTANEOUS EARTH FAULT RELAY FLAGGED",
    "INST O/C": "INSTANTANEOUS OVER CURRENT RELAY FLAGGED",
    "OC/EF": "OVER CURRENT AND EARTH FAULT RELAY FLAGGED",
    "INST OC/EF": "INSTANTANEOUS OVER CURRENT AND EARTH FAULT RELAY FLAGGED",
}


def _normalize_code(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().upper())


FAULT_CODE_LOOKUP = {_normalize_code(k): v for k, v in FAULT_CODES.items()}


def classify_cell(value):
    """Classify a single hourly load cell.

    Returns (load_mw: float, cause: str | None, kind: "numeric"|"fault"|"wrong_format").
    Real numbers pass through with no cause. Recognized fault codes are zeroed
    out with their description as the cause. Anything else (including blank
    cells) is zeroed out with a "Wrong Data Format" cause naming what was found.
    """
    if isinstance(value, bool):
        return 0.0, f"Wrong Data Format ({value})", "wrong_format"
    if isinstance(value, (int, float)):
        return float(value), None, "numeric"
    if value is None:
        return 0.0, "Wrong Data Format (blank)", "wrong_format"

    s = str(value).strip()
    if s == "":
        return 0.0, "Wrong Data Format (blank)", "wrong_format"

    try:
        return float(s), None, "numeric"
    except ValueError:
        pass

    cause = FAULT_CODE_LOOKUP.get(_normalize_code(s))
    if cause is not None:
        return 0.0, cause, "fault"

    return 0.0, f"Wrong Data Format ({s})", "wrong_format"


def normalize_time_header(value) -> str:
    """Normalize an hour-header cell (e.g. '01:00' or a datetime.time) to 'HH:MM'."""
    if isinstance(value, dt_time):
        return f"{value.hour:02d}:{value.minute:02d}"
    if value is None:
        return ""
    return str(value).strip()


def build_merge_fill_map(ws, col_idx: int, max_row: int) -> dict:
    """Forward-fill a single merged column so every row gets the merge's value.

    openpyxl only stores a value in the top-left cell of a merged range; every
    other cell in that range reads back as None. ACC and Station columns are
    vertically merged across the rows they cover, so this reconstructs the
    effective value for every row.
    """
    values = {r: ws.cell(row=r, column=col_idx).value for r in range(1, max_row + 1)}
    for mc in ws.merged_cells.ranges:
        if mc.min_col == col_idx and mc.max_col == col_idx:
            anchor = ws.cell(row=mc.min_row, column=col_idx).value
            for r in range(mc.min_row, mc.max_row + 1):
                values[r] = anchor
    return values


def build_merge_fill_map_row(ws, row_idx: int, max_col: int) -> dict:
    """Forward-fill a single merged row so every column gets the merge's value.

    Horizontal analogue of build_merge_fill_map: used for sheets (like the
    transposed transformer-load tracker) where ACC/Station are merged across
    the columns of the transformers they cover, rather than down rows.
    """
    values = {c: ws.cell(row=row_idx, column=c).value for c in range(1, max_col + 1)}
    for mc in ws.merged_cells.ranges:
        if mc.min_row == row_idx and mc.max_row == row_idx:
            anchor = ws.cell(row=row_idx, column=mc.min_col).value
            for c in range(mc.min_col, mc.max_col + 1):
                values[c] = anchor
    return values


def normalize_hour_code(value) -> str:
    """Normalize a compact hour code (e.g. '0100', '2400', or a datetime.time) to 'HH:MM'."""
    if isinstance(value, dt_time):
        return f"{value.hour:02d}:{value.minute:02d}"
    if value is None:
        return ""
    s = str(value).strip()
    if ":" in s:
        return s
    digits = re.sub(r"\D", "", s)
    if len(digits) == 3:
        digits = "0" + digits
    if len(digits) != 4:
        return s
    return f"{digits[:2]}:{digits[2:]}"
