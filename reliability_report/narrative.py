"""The words around the SLA outage report's tables.

Same trust model as monthly_report/narrative.py (reused here, not
reimplemented): rule_based(facts) writes plain sentences straight from the
numbers with no API key needed; with_llm(...) asks Claude to write the
same fields from the same numbers, then checks every number Claude used
against the facts -- an untraceable number falls back to the rule-based
sentence for that section, and the report says so.

Two document shapes share this module: a regional report (TCN outages
only) and the management summary (all regions, all parties in section 2,
TCN-only for SLA/exceedance/cost). REGIONAL_SECTIONS / MANAGEMENT_SECTIONS
below are what an editable-text UI and the chat assistant (Part 2) would
list, and what render/docx_builder.py expects `text` to contain.
"""
from __future__ import annotations

import json
from pathlib import Path

from monthly_report.narrative import _numbers_in, _untraceable

SYSTEM_PROMPT = (Path(__file__).resolve().parent / "prompts" / "sla_report_system.md").read_text()

REGIONAL_SECTIONS = {
    "intro": "Two or three sentences: what this report covers (region, period, TCN-attributed 33kV feeders "
             "only), and how it compares with the previous issued period (outages and cost, up or down).",
    "cause_note": "One sentence on who is responsible for the outages in this report (TCN equipment only).",
    "sla_note": "One or two sentences explaining the band-based allowable-hours rule and the 4h/day default "
                "for feeders with no recorded band, framing the SLA Compliance table below.",
    "exceedance_note": "One or two sentences explaining how excess hours and cost are worked out for the "
                        "Exceedance & Cost table, and a reminder that these figures cover only this one period.",
    "root_cause_notes": "One paragraph per feeder or station group among the highest-cost exceedances, each "
                         "citing the specific equipment, dates and remarks behind the outages -- separate "
                         "paragraphs with a blank line. If a group's cause is not recorded in the remarks, say so.",
    "at_risk_note": "One paragraph on the feeders at 70% or more of their month-to-date allocation but not yet "
                     "over -- name them and say why cutting their downtime now still saves money this month. "
                     "Empty string if there are none.",
    "intervention_note": "One paragraph per human intervention/provisional note supplied for this region, "
                          "presented near-verbatim -- separate paragraphs with a blank line. Empty string if none.",
}

MANAGEMENT_SECTIONS = {
    "intro": "Two or three sentences: what this report brings together (all ten regions, the period, the "
             "30:70 TCN/DISCO ratio) and the headline change from the previous issued period.",
    "important_note": "The reminder that weekly figures cannot be straight-line summed to get the month-to-date "
                       "total, with the actual combined figure and what naively adding the weeks would give.",
    "cause_note": "One sentence on the network-wide biggest cause of downtime this period and how it compares "
                   "with earlier periods.",
    "kpi_note": "One or two sentences explaining how the outage-hour allocation and cost are worked out.",
    "region_exceedance_note": "One sentence: how many regions had at least one feeder over allocation, and "
                                "which had none.",
    "top_cost_note": "One sentence naming the stations/regions behind most of this period's cost.",
    "comparative_note": "One or two sentences on how to fairly compare periods of different lengths, and the "
                          "biggest swings between periods by region, naming the driving station/feeder.",
    "aggregate_note": "One or two sentences on why the month-to-date aggregate is the correct combined picture, "
                        "not the sum of the weekly figures, with the size of the gap between the two.",
    "key_observations": "One paragraph per major finding (worst stations/feeders, patterns across periods, "
                         "misattribution risks), citing specific equipment and figures -- separate paragraphs "
                         "with a blank line.",
    "intervention_note": "One paragraph per human intervention/provisional note supplied network-wide, "
                          "presented near-verbatim -- separate paragraphs with a blank line. Empty string if none.",
    "recommendations": "One recommendation per paragraph, in order of cost impact, each naming who should act "
                        "and why it saves money -- separate paragraphs with a blank line.",
    "closing_note": "A short closing reminder: month-to-date is in the aggregate section, and which sections "
                     "cover all parties versus TCN-only.",
}


def _prompt_and_payload(facts: dict, sections: dict) -> tuple[str, dict, dict]:
    schema = {"type": "object", "required": list(sections), "additionalProperties": False,
              "properties": {k: {"type": "string", "description": d} for k, d in sections.items()}}
    return SYSTEM_PROMPT, {"facts": facts}, schema


def _ask_anthropic(system, payload, schema, api_key, model) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model, max_tokens=4000, system=system,
        tools=[{"name": "write_report", "description": "The paragraphs of the SLA outage report.", "input_schema": schema}],
        tool_choice={"type": "tool", "name": "write_report"},
        messages=[{"role": "user", "content": "Figures for this report:\n\n" + json.dumps(payload, default=str)}],
    )
    return next(b.input for b in msg.content if b.type == "tool_use")


def rule_based(facts: dict, doc_type: str) -> dict:
    """No API key needed -- always available so the report still builds."""
    f = facts
    if doc_type == "regional":
        cmp = f.get("previous_issued")
        cmp_txt = ""
        if cmp:
            up_down = "up" if f["estimated_cost_ngn"] >= cmp.get("estimated_cost_ngn", 0) else "down"
            outages_clause = f"outages went from {cmp['outages']} to {f['outages']}, and estimated " if "outages" in cmp else "estimated "
            cmp_txt = (f" Compared with {cmp['period_label']}: {outages_clause}cost is {up_down} "
                       f"from ₦{cmp.get('estimated_cost_ngn', 0):,.2f} to ₦{f['estimated_cost_ngn']:,.2f}.")
        return {
            "intro": (f"This report covers TCN-attributed 33kV feeder outages in {f['region']} for {f['period_label']} "
                      f"({f['period_start']} to {f['period_end']})." + cmp_txt),
            "cause_note": "Every outage in this report is caused by TCN equipment; no DISCO-caused outages are included.",
            "sla_note": ("Each DISCO band (A-D) has a set number of hours TCN is allowed to be down; a feeder with no "
                        "recorded band is assumed Band A (4 hours/day)."),
            "exceedance_note": ("Cost below counts only the hours a feeder was down after its allocation ran out, using "
                                "the actual load recorded during those extra hours. These figures cover only this period."),
            "root_cause_notes": "\n\n".join(
                f"{r['station']} / {r['feeder']}: {r['excess_hrs']:.1f} excess hours, ₦{r['estimated_cost_ngn']:,.2f}. "
                f"Cause not recorded in the remarks for these events; recommend logging a specific cause."
                for r in f.get("top_cost_feeders", [])[:5]
            ),
            "at_risk_note": "\n\n".join(
                f"{r['station']} / {r['feeder']} has used {r['used_pct']*100:.0f}% of its month-to-date allocation."
                for r in f.get("at_risk_feeders", [])
            ),
            "intervention_note": "\n\n".join(n["text"] for n in f.get("human_notes", []) if n["kind"] == "intervention"),
        }
    else:
        return {
            "intro": (f"This report brings together 33kV feeder outage data from all ten TCN regions for "
                      f"{f['period_label']} ({f['period_start']} to {f['period_end']})."),
            "important_note": (f"Weekly figures cannot be straight-line summed to get the month-to-date total. "
                                f"The true combined exposure is ₦{f.get('mtd_estimated_cost_ngn', 0):,.2f}."),
            "cause_note": f"{(f.get('top_cause') or ('Unspecified', 0))[0]} is the largest share of outages this period.",
            "kpi_note": "Each feeder's allowable outage duration is based on its DISCO band (or a 4 hours/day default).",
            "region_exceedance_note": f"{f.get('regions_with_exceedance', 0)} of {f['regions']} regions had at least one feeder over allocation.",
            "top_cost_note": "See the table above for the highest-cost feeder exceedances this period.",
            "comparative_note": "Periods of different lengths are not directly comparable on raw totals; use average duration for a fairer comparison.",
            "aggregate_note": "Section 6 holds the correct combined picture for the month, calculated as one continuous period, not the sum of weeks.",
            "key_observations": "\n\n".join(
                f"{r['station']} / {r['feeder']}: {r['excess_hrs']:.1f} excess hours, ₦{r['estimated_cost_ngn']:,.2f}."
                for r in f.get("top_cost_feeders", [])[:5]
            ),
            "intervention_note": "\n\n".join(n["text"] for n in f.get("human_notes", []) if n["kind"] == "intervention"),
            "recommendations": "\n\n".join(
                f"Investigate {r['station']} / {r['feeder']}, the largest cost exposure this period."
                for r in f.get("top_cost_feeders", [])[:3]
            ),
            "closing_note": ("Section 6 holds the accurate month-to-date picture. Section 2 covers all outages "
                              "whatever the party; Sections 3, 4 and 6 cover TCN-caused outages only."),
        }


def with_llm(facts: dict, doc_type: str, provider: str, api_key: str, model: str) -> tuple[dict, list[str]]:
    """provider: 'anthropic' (only one wired up so far). Returns
    (text_by_section, notes). Any paragraph with a number not in the
    facts is replaced with the rule-based paragraph, and the report says so."""
    sections = REGIONAL_SECTIONS if doc_type == "regional" else MANAGEMENT_SECTIONS
    system, payload, schema = _prompt_and_payload(facts, sections)
    if provider != "anthropic":
        raise ValueError(f"unsupported provider: {provider}")
    out = _ask_anthropic(system, payload, schema, api_key, model)
    allowed = _numbers_in(payload)
    fallback = rule_based(facts, doc_type)
    notes, final = [], {}
    for k in sections:
        text = (out.get(k) or "").strip()
        bad = _untraceable(text, allowed)
        if bad:
            notes.append(f"{k}: used the rule-based text because the AI version contained numbers not in the data ({', '.join(bad)})")
            final[k] = fallback[k]
        else:
            final[k] = text if text else fallback[k]
    return final, notes
