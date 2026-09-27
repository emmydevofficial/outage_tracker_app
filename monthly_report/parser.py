"""Parse the TCC "Monthly Load Data" workbooks into clean, typed rows.

Every region uses the same three-sheet template (transformer, feeder, line).
This module finds the header row on each sheet, maps the columns to fixed
field names, and repairs the common entry problems:

* dates where Excel swapped day and month (06/07/2026 read as 6 June)
* dates typed as text (28/07/2026, 28 - 7 - 2026)
* numbers typed as text (268743.5MWH, 322643..9)
* numbers that Excel displays as dates (an Amps value shown as 09/02/1900)
* status words written in number columns (O/S, NO METER, ON SOAK)
* substation names that are only written on the first row of a group

Nothing is guessed silently. Every repair is recorded in `issues` so the
report can say how many cells were corrected, and the raw value is kept.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import openpyxl

SHEET_KINDS = ("transformer", "feeder", "line")

# The only 10 valid regions. Detection always resolves to one of these
# (or None) -- never a free-text guess, from a filename, a workbook's own
# "Region:" field, or the AI fallback in narrative.guess_region().
KNOWN_REGIONS = ["Abuja", "Bauchi", "Benin", "Enugu", "Kaduna", "Kano",
                 "Lagos", "Osogbo", "Port Harcourt", "Shiroro"]

# The blank template shipped with one filled-in example row per sheet.
# Some regions left it in, so it would appear as a real (and very loaded) unit.
TEMPLATE_EXAMPLES = {("transformer", 128.4, "19:45"), ("feeder", 24.6, "20:15"), ("line", 412.5, "19:30")}


@dataclass
class Row:
    region: str
    kind: str                     # transformer | feeder | line
    sheet_row: int
    substation: str | None
    name: str
    nomenclature: str | None
    rating_mva: float | None = None
    max_mw: float | None = None
    max_amps: float | None = None
    max_kv: float | None = None
    max_time: str | None = None   # HH:MM
    max_date: dt.date | None = None
    temp_primary_max: float | None = None
    temp_secondary_max: float | None = None
    min_mw: float | None = None
    min_amps: float | None = None
    min_kv: float | None = None
    min_time: str | None = None
    min_date: dt.date | None = None
    energy_reading: float | None = None
    status_text: str | None = None  # words found in number columns
    remarks: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw")
        return d


@dataclass
class ParseResult:
    region: str
    rows: list[Row]
    issues: list[dict]  # {region, kind, sheet_row, field, problem, raw}
    region_source: str = "given"  # 'file content' | 'filename' | 'given' | 'unresolved'


def _clean_header(h: Any) -> str:
    return re.sub(r"\s+", " ", str(h)).strip().lower()


def _field_for(h: str) -> str | None:
    if not h:
        return None
    if "energy" in h:
        return "energy_reading"
    if "remark" in h:
        return "remarks"
    if "substation" in h:
        return "substation"
    if "rating" in h:
        return "rating_mva"
    if "nomencla" in h:
        return "nomenclature"
    if h.startswith("line designation") or h.startswith("feeder name") or h.startswith("transformer nam"):
        return "name"
    side = "max" if "max" in h else "min" if "min" in h else None
    if not side:
        return None
    for key, suffix in (("(mw)", "mw"), ("(amps)", "amps"), ("voltage", "kv"), ("time", "time"),
                        ("date", "date"), ("primary", "temp_primary"), ("secondary", "temp_secondary")):
        if key in h:
            if suffix.startswith("temp"):
                return f"{suffix}_{side}"
            return f"{side}_{suffix}"
    return None


def _sheet_kind(title: str) -> str | None:
    t = title.lower()
    for k in SHEET_KINDS:
        if k in t:
            return k
    return None


def _find_header(rows: list[tuple]) -> int | None:
    for i, r in enumerate(rows[:15]):
        s = " ".join(str(c) for c in r if c is not None).lower()
        if "max load" in s and "(mw)" in s:
            return i
    return None


class _Cleaner:
    def __init__(self, region: str, kind: str, year: int, month: int, issues: list):
        self.region, self.kind, self.year, self.month = region, kind, year, month
        self.issues = issues
        self.sheet_row = 0

    def note(self, fld: str, problem: str, raw: Any):
        self.issues.append(dict(region=self.region, kind=self.kind, sheet_row=self.sheet_row,
                                field=fld, problem=problem, raw=str(raw)))

    def number(self, v: Any, fld: str) -> tuple[float | None, str | None]:
        """Returns (number, status_word)."""
        if v is None or isinstance(v, bool):
            return None, None
        if isinstance(v, (int, float)):
            return float(v), None
        if isinstance(v, dt.datetime) and v.year < 1901:
            n = float((v - dt.datetime(1899, 12, 30)).days)
            self.note(fld, "number formatted as a date", v)
            return n, None
        if isinstance(v, str):
            s = v.strip()
            if not s or s in {"-", "--"}:
                return None, None
            t = re.sub(r"(?i)\s*(mwh|kwh|mw|kv|a)\s*$", "", s).replace(",", "")
            t = re.sub(r"\.{2,}", ".", t)
            try:
                n = float(t)
                self.note(fld, "number typed as text", v)
                return n, None
            except ValueError:
                return None, s.upper()
        self.note(fld, "unreadable value", v)
        return None, None

    def date(self, v: Any, fld: str) -> dt.date | None:
        if v is None:
            return None
        if isinstance(v, dt.datetime):
            if v.year == self.year and v.month == self.month:
                return v.date()
            # Excel read DD/MM as MM/DD: 06/07/2026 became 7 June -> day=7 is really the month
            if v.year == self.year and v.day == self.month and v.month <= 31:
                self.note(fld, "day and month swapped", v)
                return dt.date(self.year, self.month, v.month)
            self.note(fld, "date outside the reporting month", v)
            return None
        if isinstance(v, str):
            m = re.match(r"\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{2,4})", v)
            if m:
                d, mo, y = (int(x) for x in m.groups())
                y = y + 2000 if y < 100 else y
                try:
                    out = dt.date(y, mo, d)
                    self.note(fld, "date typed as text", v)
                    if out.month != self.month:
                        self.note(fld, "date outside the reporting month", v)
                        return None
                    return out
                except ValueError:
                    pass
            if re.search(r"\d", v):
                self.note(fld, "unreadable date", v)
            return None
        return None

    def time(self, v: Any, fld: str) -> str | None:
        if v is None:
            return None
        if isinstance(v, dt.time):
            return f"{v.hour:02d}:{v.minute:02d}"
        if isinstance(v, dt.datetime):
            return f"{v.hour:02d}:{v.minute:02d}"
        if isinstance(v, dt.timedelta):
            mins = int(v.total_seconds() // 60) % (24 * 60)
            return f"{mins // 60:02d}:{mins % 60:02d}"
        if isinstance(v, (int, float)):
            if 0 <= v < 1:  # Excel fraction of a day
                mins = round(v * 24 * 60) % (24 * 60)
                return f"{mins // 60:02d}:{mins % 60:02d}"
            if 0 <= v <= 24:
                return f"{int(v) % 24:02d}:00"
            if 100 <= v <= 2400:  # 1930 -> 19:30
                return f"{int(v) // 100 % 24:02d}:{int(v) % 100:02d}"
            self.note(fld, "unreadable time", v)
            return None
        if isinstance(v, str):
            m = re.search(r"(\d{1,2})\s*[:.]\s*(\d{2})", v)
            if m:
                return f"{int(m.group(1)) % 24:02d}:{m.group(2)}"
            m = re.fullmatch(r"\s*(\d{3,4})\s*(hrs?)?\s*", v, re.I)
            if m:
                n = int(m.group(1))
                return f"{n // 100 % 24:02d}:{n % 100:02d}"
            return None
        return None


def _match_known_region(text: str | None) -> str | None:
    """Whole-word, case-insensitive match against the 10 real regions.
    Region names are a small closed set -- checking "does this text contain
    one of the 10 real names" is strictly more reliable than any heuristic
    that tries to parse structure out of a filename or label, and it can
    never invent an 11th region."""
    if not text:
        return None
    t = re.sub(r"\s+", " ", str(text)).strip()
    for r in KNOWN_REGIONS:
        pattern = r"(?<![A-Za-z])" + r"[\s\-]+".join(re.escape(w) for w in r.split(" ")) + r"(?![A-Za-z])"
        if re.search(pattern, t, re.I):
            return r
    return None


def _region_from_workbook(wb) -> str | None:
    """Every real TCC workbook has a 'Reporting Date: ... Region: ...
    Prepared by: ...' line near the top of each sheet -- the region the
    preparing engineer actually typed in, which is more authoritative than
    the filename when it's filled in. Some regions leave it blank some
    months, so this can legitimately return None."""
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True, max_row=8):
            for cell in row:
                if not isinstance(cell, str) or "region" not in cell.lower():
                    continue
                m = re.search(r"region\s*:\s*_*\s*([^_]*?)\s*_*\s*(?:prepared by|$)", cell, re.I)
                if m:
                    found = _match_known_region(m.group(1))
                    if found:
                        return found
    return None


def header_snippet(wb, max_rows: int = 8) -> str:
    """Early-row text across every sheet, joined -- passed to the AI
    fallback (narrative.guess_region) as context when neither
    deterministic tier resolves a region, so it has more to go on than
    just the filename."""
    parts = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True, max_row=max_rows):
            for cell in row:
                if isinstance(cell, str) and cell.strip():
                    parts.append(cell.strip())
    return " | ".join(parts)


def _region_from_filename(path: Path) -> str | None:
    """Filename fallback for when the workbook's own Region: field is
    blank. Matches against the 10 known regions rather than trying to
    parse filename structure -- real filenames are wildly inconsistent
    ("TCC Monthly Load Data for August 2026 - ENUGU REGION.xlsx",
    "8. Kaduna Region August TCC Month-End Report.xlsx", "Shiroro Monthly
    Load Data.xlsx" all name their region as a plain substring, nothing
    else about their structure is consistent)."""
    return _match_known_region(path.stem)


def detect_region(path: Path, wb) -> tuple[str | None, str]:
    """(region, source). source is 'file content', 'filename', or
    'unresolved' -- never invents a region; the caller decides what to do
    when it's unresolved (ask the AI fallback, or ask a person)."""
    region = _region_from_workbook(wb)
    if region:
        return region, "file content"
    region = _region_from_filename(path)
    if region:
        return region, "filename"
    return None, "unresolved"


def parse_workbook(path: str | Path, year: int, month: int, region: str | None = None) -> ParseResult:
    path = Path(path)
    wb = openpyxl.load_workbook(path, data_only=True)
    region_source = "given"
    if not region:
        region, region_source = detect_region(path, wb)
        region = region or "Unresolved"
    out: list[Row] = []
    issues: list[dict] = []

    for ws in wb.worksheets:
        kind = _sheet_kind(ws.title)
        if not kind:
            continue
        rows = list(ws.iter_rows(values_only=True))
        hi = _find_header(rows)
        if hi is None:
            issues.append(dict(region=region, kind=kind, sheet_row=0, field="sheet",
                               problem="header row not found", raw=ws.title))
            continue
        cols = [_field_for(_clean_header(h)) if h is not None else None for h in rows[hi]]
        cl = _Cleaner(region, kind, year, month, issues)
        current_sub = None
        for offset, r in enumerate(rows[hi + 1:], start=hi + 2):
            cl.sheet_row = offset
            rec = {c: r[j] for j, c in enumerate(cols) if c and j < len(r) and c not in ()}
            name = rec.get("name")
            sub = rec.get("substation")
            # skip repeated banners ("Reporting Date: ...", area control centre titles)
            joined = " ".join(str(x) for x in r if x is not None).lower()
            if "reporting date" in joined or "control centre" in joined and not name:
                continue
            if sub is not None and str(sub).strip() and str(sub).strip().lower() != "substation":
                current_sub = re.sub(r"\s+", " ", str(sub)).strip()
            if name is None or not str(name).strip():
                continue
            name = re.sub(r"\s+", " ", str(name)).strip()
            row = Row(region=region, kind=kind, sheet_row=offset, substation=current_sub,
                      name=name, nomenclature=(str(rec["nomenclature"]).strip() if rec.get("nomenclature") else None),
                      raw={k: (str(v) if v is not None else None) for k, v in rec.items()})
            statuses = []
            for fld in ("rating_mva", "max_mw", "max_amps", "max_kv", "min_mw", "min_amps", "min_kv",
                        "temp_primary_max", "temp_secondary_max", "energy_reading"):
                if fld in rec:
                    n, word = cl.number(rec[fld], fld)
                    setattr(row, fld, n)
                    if word and fld in ("max_mw", "rating_mva", "energy_reading"):
                        statuses.append(word)
            for fld in ("max_date", "min_date"):
                setattr(row, fld, cl.date(rec.get(fld), fld))
            for fld in ("max_time", "min_time"):
                setattr(row, fld, cl.time(rec.get(fld), fld))
            rem = rec.get("remarks")
            row.remarks = str(rem).strip() if rem is not None and str(rem).strip() else None
            row.status_text = " | ".join(dict.fromkeys(statuses)) or None
            if (kind, row.max_mw, row.max_time) in TEMPLATE_EXAMPLES:
                cl.note("row", "template example row left in (dropped)", name)
                continue
            out.append(row)
    return ParseResult(region=region, rows=out, issues=issues, region_source=region_source)
