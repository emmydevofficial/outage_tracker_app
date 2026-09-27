"""The words around the charts.

Two sources, same shape:

* `rule_based(facts)` writes plain sentences straight from the numbers.
  It needs no API key, so the report always generates.
* `with_claude(facts, api_key, model)` asks Claude to write the same fields
  from the same numbers, then checks that every number Claude used exists
  in the input. If a number cannot be traced, that field falls back to the
  rule-based sentence and the report records why.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .parser import KNOWN_REGIONS

SECTIONS = {
    "lede": "Two or three sentences under the report title: how much came in, the biggest operational risk this month, and the state of the returns.",
    "capacity": "Where transformer capacity is out: units and MVA unavailable, which regions carry most of it, and capacity that exists but shows no use (on soak, no load, no data).",
    "loading": "How hard the transformers are working: how many peaked at 80% or more, which substations lead, how many never passed 40% and what that suggests, and any winding temperature outlier.",
    "n1": "The N-1 screen: groups with two or more in-service units at the same ratio, how many would be overloaded at the monthly peak if the largest unit tripped, how many run on one unit, and which entries need confirming first.",
    "voltage": "Line flows and voltages: count of readings outside the band, the regions with low and high patterns and the likely operational cause, and the limit that line thermal ratings are not in the template.",
    "boundary": "Boundary lines reported by two TCCs: how many pairs, how many disagree by more than 10%, and why settling these matters.",
    "timing": "When the peak happens: share of feeder maxima in the evening hours, the busiest hour, and whether the early-month clustering looks like a real event or a default entry.",
    "feeders": "Feeders: how many reported a numeric maximum, the share whose minimum fell to 0.2 MW or less and what that means on a 33 kV feeder, and the worst region.",
    "energy": "Energy meters: coverage of readings by equipment type. This month's readings are the baseline; consumption in MWh is the difference from the previous month's closing reading.",
    "data": "State of the returns: how many cells were corrected and the main kinds, template example rows found, and what the regions should change in next month's return.",
}


def rule_based(f: dict) -> dict:
    c, u, ld, n1, v, b, fd, d = (f[k] for k in ("counts", "unavailable", "loading", "n1", "voltage", "boundary", "feeders", "data"))
    top_u = ", ".join(f"{r} ({m:,.0f} MVA)" for r, m in u["top_regions"])
    by_type = d["by_type"]
    swapped = by_type.get("day and month swapped", 0)
    examples = by_type.get("template example row left in (dropped)", 0)
    hottest = ld.get("hottest")
    return dict(
        lede=(f"{c['transformers']} transformers ({c['transformer_mva']:,.0f} MVA), {c['feeders']} feeders and {c['lines']} line entries came in. "
              f"{u['mva']:,.0f} MVA of transformation was out of service, faulty or under works; {n1['fail']} substation groups cannot carry their "
              f"{f['month_label'].split()[0]} peak if their largest unit trips; and {d['total']:,} cells had to be corrected before the returns could be used."),
        capacity=(f"{u['units']} transformers ({u['mva']:,.0f} MVA) were unavailable. "
                  + (f"{top_u} carry most of it. " if len(u["top_regions"]) > 1 else "")
                  + 
                  "Units on soak, energised with no load, or with no reading at all are capacity TCN owns but cannot yet show it is using."),
        loading=(f"No transformer reported a peak above its rating" if ld["n_over_100"] == 0 else f"{ld['n_over_100']} transformers peaked above their rating")
                + f", and {ld['n_80']} of {ld['n_with_loading']} peaked at 80% or more, led by {', '.join(dict.fromkeys(ld['top3']))}. "
                  f"{ld['n_under_40']} never passed 40%, which points to limits downstream or in supply rather than in the transformer."
                + (f" The hottest winding reported was {hottest['temp']:.0f} °C at {hottest['name']} ({hottest['region']})." if hottest else ""),
        n1=(f"Of {n1['groups_multi']} substation groups with two or more in-service transformers at the same ratio, {n1['fail']} would be overloaded at "
            f"their monthly peak if the largest unit tripped. Another {n1['single']} groups had only one in-service transformer, so any trip is a supply loss."
            + (f" Confirm the entries for {', '.join(n1['identical'])} first: their transformers were returned with identical figures." if n1["identical"] else "")),
        voltage=(f"{v['excursions']} voltage readings fell outside the normal band. {v['worst_low_region']} shows the clearest low-voltage pattern and "
                 f"{v['worst_high_region']} the most high readings. Line thermal ratings are not in the template, so flows can be ranked but not yet "
                 "expressed as a percentage of capacity."),
        boundary=("Boundary lines are compared only when the returns of both regions at either end are uploaded; none could be paired in this selection."
                  if b["pairs"] == 0 else f"{b['pairs']} interconnectors were reported by the TCC at each end, and {b['flagged']} of them disagree by more than 10%. "
                  "Where the two maxima differ that much, one end's meter, CT ratio or transcription is wrong, and it is worth settling before these figures "
                  "feed transmission performance reporting."),
        timing=(f"{fd['evening_pct']}% of feeder maxima were logged between 19:00 and 21:59, and {int(fd['busiest_hour']):02d}:00 was the busiest single hour. "
                f"{fd['early_days']} feeder maxima were logged on the first two days of the month; check whether that is a real event or a default entry."),
        feeders=(f"{fd['numeric_max']} feeders reported a numeric maximum. {fd['dip_pct']}% had a monthly minimum of 0.2 MW or less, which on a 33 kV feeder "
                 f"means it was effectively off at some point: an outage, load shedding, or a DisCo not taking load. {fd['dip_worst']} has the highest share."),
        energy=(("This month's closing meter readings are stored as the baseline. From next month the review shows MWh per feeder and transformer as the "
                 "difference between closing readings, with any negative or impossible result flagged for checking.") if "energy" not in f else
                (f"{f['energy']['meters_checked']} meters passed the checks, and the checked 33 kV feeder meters recorded {f['energy']['total_mwh']:,} MWh delivered this month. "
                 f"{f['energy']['meters_flagged']} were held back: no reading in one of the two months, a negative difference (meter reset or replaced), "
                 f"or more energy than the unit's maximum MW over {f['energy']['hours']} hours could deliver. Confirm units and meter multipliers before these figures are used for billing.")),
        data=(f"{d['total']:,} cells were corrected on the way in, including {swapped:,} dates where Excel swapped the day and month. "
              + (f"{examples} template example rows had been left in and were removed. " if examples else "")
              + f"{d['shared_codes']} rows share an equipment code with another row in the same sheet, which makes month-to-month matching unreliable. "
                "Regions should enter dates as real dates, keep status words in the Remarks column, and give each unit a unique code."),
    )


_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def _numbers_in(obj) -> set[float]:
    s = json.dumps(obj, default=str)
    return {float(x.replace(",", "")) for x in _NUM.findall(s)}


def _untraceable(text: str, allowed: set[float]) -> list[str]:
    bad = []
    for m in _NUM.findall(text):
        v = float(m.replace(",", ""))
        if v in allowed or v in {0, 1, 2, 10, 24, 33, 40, 80, 90, 100, 132, 330} or 1900 < v < 2100 or v <= 31 and v == int(v):
            continue  # days, common thresholds, voltages, years
        if any(abs(v - a) <= max(0.5, 0.005 * abs(a)) for a in allowed):
            continue  # rounding of a supplied figure
        bad.append(m)
    return bad


def _prompt_and_payload(facts: dict, context: dict) -> tuple[str, dict, dict]:
    system = (Path(__file__).resolve().parent.parent / "prompts" / "monthly_review_system.md").read_text()
    payload = {"facts": facts, "detail": context}
    schema = {"type": "object", "required": list(SECTIONS), "additionalProperties": False,
              "properties": {k: {"type": "string", "description": d} for k, d in SECTIONS.items()}}
    return system, payload, schema


def _ask_anthropic(system, payload, schema, api_key, model) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model, max_tokens=4000, system=system,
        tools=[{"name": "write_review", "description": "The paragraphs of the monthly operations review.", "input_schema": schema}],
        tool_choice={"type": "tool", "name": "write_review"},
        messages=[{"role": "user", "content": "Figures for this month's review:\n\n" + json.dumps(payload, default=str)}],
    )
    return next(b.input for b in msg.content if b.type == "tool_use")


def _ask_openai(system, payload, schema, api_key, model) -> dict:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    resp = client.responses.create(
        model=model, instructions=system,
        input="Figures for this month's review:\n\n" + json.dumps(payload, default=str),
        tools=[{"type": "function", "name": "write_review", "description": "The paragraphs of the monthly operations review.",
                "parameters": schema, "strict": True}],
        tool_choice={"type": "function", "name": "write_review"},
    )
    call = next(o for o in resp.output if o.type == "function_call")
    return json.loads(call.arguments)


def with_llm(facts: dict, context: dict, provider: str, api_key: str, model: str) -> tuple[dict, list[str]]:
    """provider: 'anthropic' (Claude) or 'openai'. Returns (text_by_section, notes).

    Either way the model only writes sentences. Any paragraph containing a number that is not in
    the figures it was given is replaced with the rule-based paragraph, and the report says so.
    """
    system, payload, schema = _prompt_and_payload(facts, context)
    ask = {"anthropic": _ask_anthropic, "openai": _ask_openai}[provider]
    out = ask(system, payload, schema, api_key, model)
    allowed = _numbers_in(payload)
    fallback = rule_based(facts)
    notes, final = [], {}
    for k in SECTIONS:
        text = (out.get(k) or "").strip()
        bad = _untraceable(text, allowed)
        if not text or bad:
            notes.append(f"{k}: used the rule-based text because the AI version contained numbers not in the data ({', '.join(bad)})" if bad
                         else f"{k}: the AI returned nothing, used the rule-based text")
            final[k] = fallback[k]
        else:
            final[k] = text
    return final, notes


def with_claude(facts: dict, context: dict, api_key: str, model: str) -> tuple[dict, list[str]]:
    return with_llm(facts, context, "anthropic", api_key, model)


def guess_region(filename: str, header_text: str, provider: str, api_key: str, model: str) -> str | None:
    """Last-resort region guess, only called when parser.detect_region()'s
    two deterministic tiers (the workbook's own "Region:" field, then the
    filename) both come back unresolved.

    The model is structurally constrained to the 10 real regions (a JSON
    schema enum, not a free-text answer it could hallucinate an 11th name
    into), and the result is validated against KNOWN_REGIONS again here
    regardless -- if the call fails or returns anything else, this
    returns None and the caller treats the file as still unresolved."""
    schema = {"type": "object", "required": ["region"], "additionalProperties": False,
              "properties": {"region": {"type": "string", "enum": KNOWN_REGIONS}}}
    system = ("You identify which of a fixed list of Nigerian power grid regions a TCC monthly "
              "load-return workbook belongs to, from its filename and a snippet of its own header "
              "text. Only ever answer with one of the given region names.")
    payload = {"filename": filename, "header_text_snippet": header_text[:2000]}
    try:
        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model=model, max_tokens=100, system=system,
                tools=[{"name": "pick_region", "description": "The workbook's region.", "input_schema": schema}],
                tool_choice={"type": "tool", "name": "pick_region"},
                messages=[{"role": "user", "content": json.dumps(payload)}],
            )
            out = next(b.input for b in msg.content if b.type == "tool_use")
        elif provider == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            resp = client.responses.create(
                model=model, instructions=system, input=json.dumps(payload),
                tools=[{"type": "function", "name": "pick_region", "description": "The workbook's region.",
                        "parameters": schema, "strict": True}],
                tool_choice={"type": "function", "name": "pick_region"},
            )
            call = next(o for o in resp.output if o.type == "function_call")
            out = json.loads(call.arguments)
        else:
            return None
    except Exception:
        return None
    region = out.get("region")
    return region if region in KNOWN_REGIONS else None
