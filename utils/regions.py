"""
Canonical region list shared by the upload pages, the access-control helpers
in utils/auth.py, and the User Management page. Single source of truth so
every part of the app agrees on the 10 regions and their spelling.
"""

REGIONS = [
    "Abuja", "Bauchi", "Benin", "Enugu", "Kaduna",
    "Kano", "Lagos", "Osogbo", "Port-Harcourt", "Shiroro",
]

_REGIONS_BY_UPPER = {r.upper(): r for r in REGIONS}


def normalize_region(name) -> str:
    """Map a region string to its canonical spelling, case-insensitively.

    Sheets in the wild store the region name with inconsistent casing (e.g.
    'OSOGBO' straight from an Excel cell vs 'Osogbo' elsewhere). Matching
    against the canonical list keeps every table consistent. If the value
    isn't one of the 10 known regions, it's returned stripped/title-cased
    rather than dropped, so unrecognized-but-real region names don't vanish.
    """
    if name is None:
        return ""
    s = str(name).strip()
    if not s:
        return ""
    return _REGIONS_BY_UPPER.get(s.upper(), s.title())
