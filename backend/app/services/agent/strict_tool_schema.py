"""Anthropic strict custom-tool JSON Schema compatibility checks.

Raw tool `input_schema` dicts are sent to the provider as-is. Strict tool use
accepts only a subset of JSON Schema; unsupported keywords yield HTTP 400
before any model generation. Keep bounds in server-side validators instead.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Keywords that must never appear under a strict custom tool input_schema.
# minItems is handled separately: only 0 and 1 are provider-supported.
_UNSUPPORTED_STRICT_KEYWORDS = frozenset(
    {
        "maxItems",
        "maxLength",
        "minLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "pattern",
        "uniqueItems",
        "contains",
        "propertyNames",
        "minProperties",
        "maxProperties",
    }
)


def iter_unsupported_strict_keyword_paths(
    schema: Any,
    *,
    path: str = "$",
) -> list[str]:
    """Return JSON-pointer-like paths of unsupported strict-schema keywords."""

    findings: list[str] = []
    if isinstance(schema, Mapping):
        for key, value in schema.items():
            child = f"{path}.{key}"
            if key in _UNSUPPORTED_STRICT_KEYWORDS:
                findings.append(child)
            elif key == "minItems" and value not in (0, 1):
                findings.append(child)
            findings.extend(iter_unsupported_strict_keyword_paths(value, path=child))
    elif isinstance(schema, list):
        for index, item in enumerate(schema):
            findings.extend(
                iter_unsupported_strict_keyword_paths(item, path=f"{path}[{index}]")
            )
    return findings


def assert_strict_tool_schemas_compatible(tools: Iterable[Mapping[str, Any]]) -> None:
    """Fail when any strict custom tool carries an unsupported schema keyword."""

    problems: list[str] = []
    for tool in tools:
        if not tool.get("strict"):
            continue
        name = str(tool.get("name") or "<unnamed>")
        input_schema = tool.get("input_schema")
        if not isinstance(input_schema, Mapping):
            problems.append(f"{name}: missing object input_schema")
            continue
        for finding in iter_unsupported_strict_keyword_paths(input_schema):
            problems.append(f"{name}: {finding}")
    if problems:
        joined = "; ".join(problems)
        raise AssertionError(
            "strict custom tool schema uses Anthropic-unsupported keywords: "
            f"{joined}"
        )
