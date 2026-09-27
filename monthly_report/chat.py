"""Chat with the monthly review report.

Two tools, both operating on an already-built report_data (from
render.compute_report) plus the draft's current section text -- neither
tool re-parses a workbook or touches figures/tables/charts:

* get_table: read-only lookup into the detail tables already computed by
  metrics.compute() (report_data["m"]), plus the raw issues/rows.
* propose_section_edit: does NOT change anything. It runs the same
  number-traceability check narrative.with_llm() already uses on the
  original AI paragraphs (_numbers_in/_untraceable, imported not
  reimplemented) and reports whether the proposed text is safe to offer
  the user as an Accept/Reject card.

run_turn() is a manual Anthropic tool-use loop (max 6 tool calls per user
message, per the feature spec) -- there is no "auto" tool loop here on
purpose: every propose_section_edit result is surfaced to the Streamlit
layer as a pending proposal, never applied by this module.
"""
from __future__ import annotations

import json
from pathlib import Path

from .narrative import SECTIONS, _numbers_in, _untraceable

SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "report_chat_system.md").read_text()

MAX_TOOL_CALLS = 6
MAX_HISTORY_TURNS = 20
MAX_CHAT_MESSAGES = 40  # user+assistant turns per draft, not API calls

_TABLE_NAMES = ["board", "unavailable_rows", "top_loaded", "n1_fail", "single", "excursions",
                "top_flows", "boundary", "meters", "problems_by_type", "issues", "rows"]

TOOLS = [
    {
        "name": "get_table",
        "description": "Look up a detail table behind the report's charts and tables.",
        "input_schema": {
            "type": "object", "required": ["name"],
            "properties": {
                "name": {"type": "string", "enum": _TABLE_NAMES},
                "region": {"type": "string", "description": "Filter to one region. Required for 'rows' unless substation is given."},
                "substation": {"type": "string", "description": "Filter to one substation. Required for 'rows' unless region is given."},
                "limit": {"type": "integer", "description": "Max rows to return (default 50, 'rows' is capped at 100)."},
            },
        },
    },
    {
        "name": "propose_section_edit",
        "description": (
            "Propose new text for one report section. This does not change the report -- it only checks the "
            "text and returns a pending proposal for the user to accept or reject."
        ),
        "input_schema": {
            "type": "object", "required": ["section", "new_text", "reason"],
            "properties": {
                "section": {"type": "string", "enum": list(SECTIONS)},
                "new_text": {"type": "string"},
                "reason": {"type": "string", "description": "One sentence: why this rewrite."},
            },
        },
    },
]


def get_table(report_data: dict, name: str, region: str | None = None,
              substation: str | None = None, limit: int = 50) -> dict:
    if name not in _TABLE_NAMES:
        return {"error": f"unknown table '{name}', valid names: {_TABLE_NAMES}"}
    if name == "rows":
        if not region and not substation:
            return {"error": "'rows' needs a region or substation filter -- it is too large otherwise"}
        data = report_data["rows"]
        limit = min(limit or 100, 100)
    elif name == "issues":
        data = report_data["issues"]
    else:
        data = report_data["m"][name]

    if isinstance(data, list):
        if region:
            data = [r for r in data if isinstance(r, dict) and r.get("region") == region]
        if substation:
            data = [r for r in data if isinstance(r, dict) and r.get("substation") == substation]
        total = len(data)
        data = data[: (limit or 50)]
        return {"rows": json.loads(json.dumps(data, default=str)), "returned": len(data), "total_matching": total}
    return {"value": json.loads(json.dumps(data, default=str))}


def propose_section_edit(report_data: dict, text: dict, section: str, new_text: str) -> tuple[bool, list[str]]:
    if section not in SECTIONS:
        return False, [f"unknown section '{section}'"]
    allowed = _numbers_in({"facts": report_data["f"], **report_data["ctx"]})
    bad = _untraceable(new_text, allowed)
    return (not bad), bad


def _context_block(report_data: dict, text: dict) -> dict:
    payload = {
        "month": report_data["month_label"], "scope": report_data["scope"],
        "facts": report_data["f"], "current_text": text,
    }
    return {"type": "text", "text": "Report context:\n\n" + json.dumps(payload, default=str),
            "cache_control": {"type": "ephemeral"}}


def run_turn(report_data: dict, text: dict, history: list[dict], user_message: str,
             api_key: str, model: str) -> dict:
    """One chat turn. Returns dict(reply, tool_calls, proposal, usage).

    proposal is None or {section, reason, old_text, new_text} -- the last
    *validated* propose_section_edit call this turn, if any. usage is the
    summed token counts across every API round trip this turn took."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    system = [{"type": "text", "text": SYSTEM_PROMPT}, _context_block(report_data, text)]
    messages = list(history[-MAX_HISTORY_TURNS:]) + [{"role": "user", "content": user_message}]

    tool_calls: list[dict] = []
    proposal: dict | None = None
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    calls_made = 0
    stopped_early = False

    while True:
        resp = client.messages.create(model=model, max_tokens=1500, system=system, tools=TOOLS, messages=messages)
        u = resp.usage
        call_usage = {
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        print(f"[mlr_chat] {model} usage: {call_usage}")
        for k, v in call_usage.items():
            usage[k] += v

        if resp.stop_reason != "tool_use":
            reply = "".join(b.text for b in resp.content if b.type == "text")
            messages.append({"role": "assistant", "content": resp.content})
            return {"reply": reply, "tool_calls": tool_calls, "proposal": proposal, "usage": usage, "messages": messages}

        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            calls_made += 1
            if calls_made > MAX_TOOL_CALLS:
                tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                     "content": "Tool call limit reached for this message.", "is_error": True})
                stopped_early = True
                continue
            tool_calls.append({"name": block.name, "input": block.input})
            if block.name == "get_table":
                result = get_table(report_data, **block.input)
            elif block.name == "propose_section_edit":
                ok, bad = propose_section_edit(report_data, text, block.input["section"], block.input["new_text"])
                if ok:
                    proposal = {"section": block.input["section"], "reason": block.input.get("reason", ""),
                                "old_text": text.get(block.input["section"], ""), "new_text": block.input["new_text"]}
                    result = {"ok": True}
                else:
                    result = {"ok": False, "untraceable_numbers": bad}
            else:
                result = {"error": f"unknown tool '{block.name}'"}
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result, default=str)})
        messages.append({"role": "user", "content": tool_results})

        if stopped_early:
            reply = "I've reached the tool-call limit for this message -- ask again to continue."
            return {"reply": reply, "tool_calls": tool_calls, "proposal": proposal, "usage": usage, "messages": messages}
