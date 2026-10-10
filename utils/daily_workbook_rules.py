"""Every validation rule for the daily regional feeder workbook upload,
in one place -- the parser (feeder_forecast/parser.py) raises/records
these by code instead of hand-typing messages, and the upload page's
"Template rules" expander renders straight from this registry, so the
rules shown to the person preparing the file and the rules the parser
actually enforces can never drift apart.

Severities:
  reject       -- the whole file is rejected, nothing from it is saved.
  pause        -- parsing stops with two candidate values shown side by
                  side; the uploader picks one (or cancels) before
                  anything is saved.
  row_skip     -- that one row is skipped; the rest of the file is saved.
  cell_warning -- the cell is stored as NULL or flagged; the row is saved.
"""
from __future__ import annotations

from dataclasses import dataclass

SEVERITIES = ("reject", "pause", "row_skip", "cell_warning")


@dataclass(frozen=True)
class Rule:
    code: str
    severity: str
    title: str
    description: str


RULES: dict[str, Rule] = {
    r.code: r for r in [
        Rule("R01", "reject", "Not a valid Excel file", "The file is not .xlsx or could not be opened."),
        Rule("R03", "reject", "Missing required sheet", 'Sheet "Control" or "Record and Accounting" is missing.'),
        Rule("R04", "reject", "Date cannot be resolved",
             "Control!L4 is blank or not a real date and the filename date can't be read either, "
             "or Control!L4 is a real date in the future."),
        Rule("R05", "pause", "Region or date mismatch",
             "The filename and the sheet disagree on the region or the date."),
        Rule("R06", "reject", "Template layout changed",
             'Row 2 hour labels (columns M, Q, U ... DA) or the row 3 column headers do not match '
             "the expected template -- a column was likely inserted or deleted."),
        Rule("R07", "reject", "No TOTAL row found", 'No row has "TOTAL" in column A.'),
        Rule("R08", "reject", "No feeder rows", "Fewer than 1 feeder row was found."),
        Rule("R09", "reject", "Region not permitted", "You are not allowed to upload data for this region."),
        Rule("R10", "reject", "Region cannot be resolved",
             "Control!L3 is blank and the filename region can't be read either, or Control!L3 holds "
             "a value that is not one of the 10 regions."),
        Rule("R11", "reject", "A row's region does not match",
             "A row's column A region is different from the resolved region -- this usually means "
             "rows were pasted in from another region's workbook. There is no override."),
        Rule("R12", "cell_warning", "Region taken from filename", "Control!L3 was blank; the region was taken from the filename."),
        Rule("R13", "pause", "Date taken from filename",
             "Control!L4 is blank. The filename has a date -- confirm whether to use it."),
        Rule("R14", "pause", "Opening readings look stale",
             "Most opening meter readings don't follow on from the previous day's closing readings, "
             "or this file looks identical to another date's file -- the date in Control!L4 may be wrong."),
        Rule("R15", "cell_warning", "Blank row region filled", "Column A was blank on some rows; filled with the resolved region."),
        Rule("R20", "row_skip", "Missing feeder or station name", "The feeder name or station is blank on a row that has numbers in it."),
        Rule("R21", "row_skip", "Duplicate feeder row", "The same station and feeder appears more than once; the first row is kept."),
        Rule("R22", "row_skip", "Feeder not matched", "This feeder could not be matched to the feeder registry; it's held for review."),
        Rule("R30", "cell_warning", "Text in numeric cell", "A numeric cell contained text (e.g. \"OFF\", \"N/A\"); stored as blank."),
        Rule("R31", "cell_warning", "Negative forecast", "The forecast value was negative; stored as blank."),
        Rule("R32", "cell_warning", "Meter reading decreased",
             "This reading is lower than the previous reading that day; the energy for that hour is left blank."),
        Rule("R33", "cell_warning", "Hourly energy above limit",
             "The hour's energy is more than 50% above the TCN limit, or more than 2x the rating."),
        Rule("R34", "cell_warning", "Blank band", "The feeder's band was blank; pricing falls back to the SLA band or Band A."),
        Rule("R35", "cell_warning", "Opening reading mismatch", "The opening reading differs from the previous day's 24:00 reading."),
    ]
}


def issue(code: str, **kwargs) -> dict:
    """A ready-to-store daily_workbook_issues row. kwargs: sheet, cell,
    station_raw, feeder_raw, value_found, expected -- whichever apply."""
    rule = RULES[code]
    action = {
        "reject": "file rejected", "pause": "paused for confirmation",
        "row_skip": "row skipped", "cell_warning": "cell set to NULL" if "value_found" in kwargs else "flagged",
    }[rule.severity]
    return dict(rule_code=code, action=kwargs.pop("action", action), **kwargs)


def rules_by_severity() -> dict[str, list[Rule]]:
    out: dict[str, list[Rule]] = {s: [] for s in SEVERITIES}
    for r in RULES.values():
        out[r.severity].append(r)
    return out
