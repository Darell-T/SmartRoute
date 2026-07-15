"""Pure assertion-evaluation engine for the golden-query eval harness.

No model, no network, no filesystem -- every function here takes plain data
(a trace-like object, the final assistant text, the SSE events for the
query, and an optional `call_log` for the one special-cased `derived`
assertion) and returns `AssertionResult`s. This is what makes the engine
unit-testable without ever touching `run_agent_turn` (see
`backend/tests/test_agent_evals.py`).

See `golden_queries.yaml`'s header comment for the assertion vocabulary this
module implements; keep the two in sync.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timedelta

# ---- trace-like duck typing -------------------------------------------
#
# `trace` only needs `.tool_calls` (list[tuple[str, dict]]) and
# `.final_text` (str) -- either a real `agent.loop.TurnTrace` or a
# `types.SimpleNamespace` the runner builds by concatenating several turns'
# traces (see run_agent_evals.py, which evaluates assertions against the
# WHOLE query -- every turn's tool calls, the LAST turn's final text).


@dataclasses.dataclass
class AssertionResult:
    ok: bool
    detail: str
    spec: dict


KNOWN_KEYS = {
    "tool_called",
    "tool_not_called",
    "no_tools",
    "tool_input",
    "call_order",
    "call_count",
    "final_text",
    "derived",
}

KNOWN_TOOL_INPUT_OPS = {"contains", "equals", "absent", "matches_regex", "iso_within"}
KNOWN_FINAL_TEXT_OPS = {"matches_regex", "not_matches_regex"}
KNOWN_CALL_COUNT_OPS = {"eq", "gte"}
KNOWN_DERIVED_KINDS = {"leg2_departs_after_leg1_arrival_plus"}


class AssertionSpecError(ValueError):
    """Raised for a malformed assertion spec (unknown op/key, missing
    field) -- distinct from an assertion that ran and failed."""


def _single_key(assertion: dict) -> tuple[str, object]:
    keys = [k for k in assertion.keys() if k in KNOWN_KEYS]
    if len(keys) != 1:
        raise AssertionSpecError(f"assertion must have exactly one known key, got {list(assertion.keys())!r}")
    key = keys[0]
    return key, assertion[key]


def _get_path(data: dict, path: str):
    """Dot-path lookup into a nested dict, e.g. 'a.b.c'. Returns a sentinel
    tuple (found: bool, value) so 'absent' and 'value is None' are
    distinguishable from a genuine lookup failure."""
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _parse_iso(value: str) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt


def _calls_for(trace, name: str) -> list[dict]:
    return [tool_input for (tool_name, tool_input) in trace.tool_calls if tool_name == name]


def _first_index(trace, name: str) -> int | None:
    for index, (tool_name, _tool_input) in enumerate(trace.tool_calls):
        if tool_name == name:
            return index
    return None


# ---- individual assertion handlers -------------------------------------


def _eval_tool_called(spec: dict, trace, final_text: str, events, call_log) -> AssertionResult:
    name = spec.get("name")
    if not name:
        raise AssertionSpecError("tool_called requires 'name'")
    where = spec.get("where", "any")
    first_index = _first_index(trace, name)

    if where == "any":
        ok = first_index is not None
        detail = f"{name} called" if ok else f"{name} was never called"
    elif where == "first":
        ok = bool(trace.tool_calls) and trace.tool_calls[0][0] == name
        actual_first = trace.tool_calls[0][0] if trace.tool_calls else "(no calls)"
        detail = f"first call was {actual_first!r}" if not ok else f"{name} was the first call"
    elif isinstance(where, dict) and "none_before" in where:
        other = where["none_before"]
        other_index = _first_index(trace, other)
        ok = first_index is not None and (other_index is None or first_index < other_index)
        detail = (
            f"{name} first at {first_index}, {other} first at {other_index}"
            if not ok
            else f"{name} called before {other}"
        )
    else:
        raise AssertionSpecError(f"tool_called.where must be 'any', 'first', or {{none_before: ...}}, got {where!r}")

    return AssertionResult(ok=ok, detail=detail, spec={"tool_called": spec})


def _eval_tool_not_called(spec: dict, trace, final_text: str, events, call_log) -> AssertionResult:
    name = spec.get("name")
    if not name:
        raise AssertionSpecError("tool_not_called requires 'name'")
    ok = _first_index(trace, name) is None
    detail = f"{name} was not called" if ok else f"{name} was called (unexpected)"
    return AssertionResult(ok=ok, detail=detail, spec={"tool_not_called": spec})


def _eval_no_tools(spec, trace, final_text: str, events, call_log) -> AssertionResult:
    if not spec:
        raise AssertionSpecError("no_tools must be truthy (no_tools: true)")
    ok = len(trace.tool_calls) == 0
    detail = "no tools called" if ok else f"{len(trace.tool_calls)} tool call(s) made: {[c[0] for c in trace.tool_calls]}"
    return AssertionResult(ok=ok, detail=detail, spec={"no_tools": spec})


def _values_contains(container, value) -> bool:
    if isinstance(container, (list, tuple, set)):
        for item in container:
            if isinstance(item, str) and isinstance(value, str):
                if item.strip().lower() == value.strip().lower():
                    return True
            elif item == value:
                return True
        return False
    if isinstance(container, str) and isinstance(value, str):
        return value.strip().lower() in container.lower()
    return False


def _values_equal(actual, expected) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.strip().lower() == expected.strip().lower()
    return actual == expected


def _eval_tool_input(spec: dict, trace, final_text: str, events, call_log) -> AssertionResult:
    name = spec.get("name")
    path = spec.get("path")
    op = spec.get("op")
    if not name or not path or op is None:
        raise AssertionSpecError("tool_input requires 'name', 'path', and 'op'")
    if op not in KNOWN_TOOL_INPUT_OPS:
        raise AssertionSpecError(f"tool_input.op must be one of {sorted(KNOWN_TOOL_INPUT_OPS)}, got {op!r}")

    call_index = int(spec.get("call_index", 0))
    calls = _calls_for(trace, name)
    if call_index >= len(calls):
        return AssertionResult(
            ok=False,
            detail=f"{name} was called {len(calls)} time(s), need call_index {call_index}",
            spec={"tool_input": spec},
        )
    tool_input = calls[call_index]
    found, value = _get_path(tool_input, path)

    if op == "absent":
        ok = (not found) or value is None
        detail = f"{name}[{call_index}].{path} = {value!r}" if not ok else f"{name}[{call_index}].{path} is absent"
    elif op == "contains":
        ok = found and _values_contains(value, spec.get("value"))
        detail = f"{name}[{call_index}].{path} = {value!r} does not contain {spec.get('value')!r}" if not ok else "ok"
    elif op == "equals":
        ok = found and _values_equal(value, spec.get("value"))
        detail = f"{name}[{call_index}].{path} = {value!r} != {spec.get('value')!r}" if not ok else "ok"
    elif op == "matches_regex":
        pattern = spec.get("value")
        ok = found and isinstance(value, str) and re.search(pattern, value, re.IGNORECASE) is not None
        detail = f"{name}[{call_index}].{path} = {value!r} does not match /{pattern}/i" if not ok else "ok"
    elif op == "iso_within":
        params = spec.get("value") or {}
        of_raw, minutes = params.get("of"), params.get("minutes")
        target = _parse_iso(of_raw)
        actual = _parse_iso(value) if found else None
        if target is None or minutes is None:
            raise AssertionSpecError(f"tool_input.op == 'iso_within' needs value: {{of, minutes}}, got {params!r}")
        if actual is None:
            ok = False
            detail = f"{name}[{call_index}].{path} = {value!r} is not a valid RFC3339 timestamp"
        else:
            delta = abs((actual - target).total_seconds()) / 60.0
            ok = delta <= float(minutes)
            detail = f"{name}[{call_index}].{path} = {value!r} is {delta:.1f}min from {of_raw} (limit {minutes})"
    else:  # pragma: no cover -- guarded by the KNOWN_TOOL_INPUT_OPS check above
        raise AssertionSpecError(f"unhandled op {op!r}")

    return AssertionResult(ok=ok, detail=detail, spec={"tool_input": spec})


def _eval_call_order(spec, trace, final_text: str, events, call_log) -> AssertionResult:
    if not isinstance(spec, list) or len(spec) < 2:
        raise AssertionSpecError("call_order requires a list of at least two tool names")
    indices = [(name, _first_index(trace, name)) for name in spec]
    missing = [name for name, index in indices if index is None]
    if missing:
        return AssertionResult(ok=False, detail=f"never called: {missing}", spec={"call_order": spec})
    ok = all(indices[i][1] < indices[i + 1][1] for i in range(len(indices) - 1))
    detail = "ok" if ok else f"out of order: {indices}"
    return AssertionResult(ok=ok, detail=detail, spec={"call_order": spec})


def _eval_call_count(spec: dict, trace, final_text: str, events, call_log) -> AssertionResult:
    name = spec.get("name")
    op = spec.get("op")
    value = spec.get("value")
    if not name or op is None or value is None:
        raise AssertionSpecError("call_count requires 'name', 'op', and 'value'")
    if op not in KNOWN_CALL_COUNT_OPS:
        raise AssertionSpecError(f"call_count.op must be one of {sorted(KNOWN_CALL_COUNT_OPS)}, got {op!r}")
    count = len(_calls_for(trace, name))
    ok = count == value if op == "eq" else count >= value
    detail = f"{name} called {count} time(s) ({op} {value})"
    return AssertionResult(ok=ok, detail=detail, spec={"call_count": spec})


def _eval_final_text(spec: dict, trace, final_text: str, events, call_log) -> AssertionResult:
    op = spec.get("op")
    pattern = spec.get("pattern")
    if op not in KNOWN_FINAL_TEXT_OPS or not pattern:
        raise AssertionSpecError(f"final_text requires op in {sorted(KNOWN_FINAL_TEXT_OPS)} and a 'pattern'")
    found = re.search(pattern, final_text or "", re.IGNORECASE) is not None
    ok = found if op == "matches_regex" else not found
    detail = f"final_text {'matches' if found else 'does not match'} /{pattern}/i"
    return AssertionResult(ok=ok, detail=detail, spec={"final_text": spec})


def _eval_derived_leg2_departs_after_leg1_arrival_plus(
    params: dict, trace, final_text: str, events, call_log
) -> AssertionResult:
    minutes = params.get("minutes")
    if minutes is None:
        raise AssertionSpecError("leg2_departs_after_leg1_arrival_plus requires 'minutes'")
    plan_trip_calls = [entry for entry in (call_log or []) if entry.get("tool") == "plan_trip"]
    if len(plan_trip_calls) < 2:
        return AssertionResult(
            ok=False,
            detail=f"expected >= 2 plan_trip calls in call_log, found {len(plan_trip_calls)}",
            spec={"derived": {"leg2_departs_after_leg1_arrival_plus": params}},
        )
    leg1, leg2 = plan_trip_calls[0], plan_trip_calls[1]
    candidates = ((leg1.get("data") or {}).get("candidates")) or []
    if not candidates:
        return AssertionResult(
            ok=False,
            detail="leg1 plan_trip fixture has no candidates to read arrives_iso from",
            spec={"derived": {"leg2_departs_after_leg1_arrival_plus": params}},
        )
    # Convention: eval fixtures list the recommended candidate first (see
    # golden_queries.yaml header and README) -- production digests carry no
    # explicit "recommended" flag, so this is a fixture-authoring contract,
    # not something read off the tool's real output shape.
    leg1_arrives = _parse_iso(candidates[0].get("arrives_iso"))
    leg2_departure = _parse_iso((leg2.get("input") or {}).get("departure_time"))
    if leg1_arrives is None:
        return AssertionResult(
            ok=False, detail="leg1 candidate[0].arrives_iso is missing/invalid", spec={"derived": params}
        )
    if leg2_departure is None:
        return AssertionResult(
            ok=False,
            detail="leg2 plan_trip call has no valid departure_time",
            spec={"derived": {"leg2_departs_after_leg1_arrival_plus": params}},
        )
    threshold = leg1_arrives + timedelta(minutes=float(minutes))
    ok = leg2_departure >= threshold
    detail = f"leg2 departs {leg2_departure.isoformat()}, need >= {threshold.isoformat()} (leg1 arrival + {minutes}min)"
    return AssertionResult(ok=ok, detail=detail, spec={"derived": {"leg2_departs_after_leg1_arrival_plus": params}})


_DERIVED_HANDLERS = {
    "leg2_departs_after_leg1_arrival_plus": _eval_derived_leg2_departs_after_leg1_arrival_plus,
}


def _eval_derived(spec: dict, trace, final_text: str, events, call_log) -> AssertionResult:
    kinds = [k for k in spec.keys() if k in KNOWN_DERIVED_KINDS]
    if len(kinds) != 1:
        raise AssertionSpecError(f"derived must have exactly one known kind, got {list(spec.keys())!r}")
    kind = kinds[0]
    return _DERIVED_HANDLERS[kind](spec[kind], trace, final_text, events, call_log)


_HANDLERS = {
    "tool_called": _eval_tool_called,
    "tool_not_called": _eval_tool_not_called,
    "no_tools": _eval_no_tools,
    "tool_input": _eval_tool_input,
    "call_order": _eval_call_order,
    "call_count": _eval_call_count,
    "final_text": _eval_final_text,
    "derived": _eval_derived,
}


def evaluate_one(assertion: dict, *, trace, final_text: str, events=None, call_log=None) -> AssertionResult:
    """Evaluate a single assertion spec. Raises `AssertionSpecError` for a
    malformed spec (unknown op/key/missing field) -- callers validating a
    query bank (run_agent_evals.py --validate) should treat that as a
    hard config error, not an assertion failure."""
    key, params = _single_key(assertion)
    return _HANDLERS[key](params, trace, final_text, events or [], call_log or [])


def evaluate_all(assertions: list[dict], *, trace, final_text: str, events=None, call_log=None) -> list[AssertionResult]:
    return [evaluate_one(assertion, trace=trace, final_text=final_text, events=events, call_log=call_log) for assertion in assertions]


def validate_spec(assertion: dict) -> str | None:
    """Static shape check with no trace/final_text needed -- used by
    `--validate`. Returns an error string, or None if the spec looks sane."""
    try:
        key, params = _single_key(assertion)
        if key == "tool_called":
            if not params.get("name"):
                return "tool_called requires 'name'"
            where = params.get("where", "any")
            if where not in ("any", "first") and not (isinstance(where, dict) and "none_before" in where):
                return f"tool_called.where invalid: {where!r}"
        elif key == "tool_not_called":
            if not params.get("name"):
                return "tool_not_called requires 'name'"
        elif key == "no_tools":
            pass
        elif key == "tool_input":
            if not params.get("name") or not params.get("path") or params.get("op") is None:
                return "tool_input requires 'name', 'path', 'op'"
            if params.get("op") not in KNOWN_TOOL_INPUT_OPS:
                return f"tool_input.op invalid: {params.get('op')!r}"
            if params.get("op") == "iso_within":
                value = params.get("value") or {}
                if "of" not in value or "minutes" not in value:
                    return "tool_input.op == 'iso_within' needs value: {of, minutes}"
        elif key == "call_order":
            if not isinstance(params, list) or len(params) < 2:
                return "call_order requires a list of >= 2 tool names"
        elif key == "call_count":
            if not params.get("name") or params.get("op") not in KNOWN_CALL_COUNT_OPS or params.get("value") is None:
                return "call_count requires 'name', 'op' in eq|gte, 'value'"
        elif key == "final_text":
            if params.get("op") not in KNOWN_FINAL_TEXT_OPS or not params.get("pattern"):
                return "final_text requires op in matches_regex|not_matches_regex and 'pattern'"
        elif key == "derived":
            kinds = [k for k in params.keys() if k in KNOWN_DERIVED_KINDS]
            if len(kinds) != 1:
                return f"derived requires exactly one known kind from {sorted(KNOWN_DERIVED_KINDS)}"
    except AssertionSpecError as exc:
        return str(exc)
    return None
