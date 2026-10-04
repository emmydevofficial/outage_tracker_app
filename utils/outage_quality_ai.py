"""AI-assisted feeder-name resolution for utils/outage_quality.py --
only called for the handful of (station, feeder) pairs the deterministic
exact/fuzzy tiers couldn't resolve. Same discipline as every other AI
feature this session (monthly_report.narrative.guess_region, the SLA
report's narrative writer): the model is structurally constrained to a
closed set of real candidates via a forced tool call, so it can never
invent a feeder that doesn't exist -- "none of these" is always a valid,
distinct answer from an actual guess.
"""
from __future__ import annotations

import json


def ai_suggest_feeder(station_typed: str, feeder_typed: str, region: str | None,
                       candidates: list[tuple[str, str]], provider: str | None,
                       api_key: str, model: str | None) -> tuple[str, str] | None:
    """candidates: list of (real_station, real_feeder) pairs to choose
    from. Returns the chosen (station, feeder) or None if the model says
    none of them are a plausible match, or the call fails."""
    if not candidates:
        return None
    options = [f"{s} | {f}" for s, f in candidates]
    schema = {
        "type": "object", "required": ["match"],
        "properties": {"match": {"type": "string", "enum": options + ["none of these"]}},
    }
    system = (
        "You help clean up a Nigerian power transmission outage log. Given a station and feeder name as typed "
        "in an uploaded file, and a list of real station|feeder combinations, pick the one the typed value most "
        "likely meant to be -- usually a typo, a digit used for a similar-looking letter (0 for O), a missing "
        "space, or a different word order. If none of the options are a plausible match, say so."
    )
    payload = dict(typed_region=region, typed_station=station_typed, typed_feeder=feeder_typed)
    try:
        if (provider or "anthropic") == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model=model, max_tokens=200, system=system,
                tools=[{"name": "pick_match", "description": "The real station/feeder the typed value likely means.",
                        "input_schema": schema}],
                tool_choice={"type": "tool", "name": "pick_match"},
                messages=[{"role": "user", "content": json.dumps(payload)}],
            )
            out = next(b.input for b in msg.content if b.type == "tool_use")
        else:
            return None
    except Exception:
        return None
    match = out.get("match")
    if not match or match == "none of these" or match not in options:
        return None
    station, feeder = match.split(" | ", 1)
    return station, feeder
