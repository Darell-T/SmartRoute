"""Normalize model-selected discovery scope after a capability choice.

This is not a pre-model intent router. It only maps values the Agent already
passed to discover_places onto canonical NYC geography.
"""

from __future__ import annotations

import re
from typing import Any

CANONICAL_BOROUGHS: tuple[str, ...] = (
    "Manhattan",
    "Brooklyn",
    "Queens",
    "Bronx",
    "Staten Island",
)

SCOPE_KINDS: frozenset[str] = frozenset(
    {"current_location", "boroughs", "nyc", "named_area"}
)

_BOROUGH_ALIASES = {
    "manhattan": "Manhattan",
    "the city": "Manhattan",
    "new york county": "Manhattan",
    "brooklyn": "Brooklyn",
    "kings": "Brooklyn",
    "kings county": "Brooklyn",
    "queens": "Queens",
    "queens county": "Queens",
    "bronx": "Bronx",
    "the bronx": "Bronx",
    "bronx county": "Bronx",
    "staten island": "Staten Island",
    "richmond": "Staten Island",
    "richmond county": "Staten Island",
}

_NYC_LOCALITY_WORDS = frozenset(
    {
        "new york",
        "new york city",
        "nyc",
        "ny",
    }
)

_UNAMBIGUOUS_ADDRESS_BOROUGH = re.compile(
    r",\s*(Manhattan|Brooklyn|Queens|Bronx|The Bronx|Staten Island)\s*,",
    re.IGNORECASE,
)


def _normalized(value: object) -> str:
    return " ".join(str(value or "").replace("\u2019", "'").casefold().split())


def canonical_borough(value: object) -> str | None:
    text = _normalized(value)
    if not text:
        return None
    if text in _BOROUGH_ALIASES:
        return _BOROUGH_ALIASES[text]
    if text.startswith("the ") and text[4:] in _BOROUGH_ALIASES:
        return _BOROUGH_ALIASES[text[4:]]
    return None


def normalize_scope(scope: object) -> tuple[dict[str, Any] | None, str | None]:
    """Return a canonical scope or a closed validation error."""

    if not isinstance(scope, dict):
        return None, "scope is required"
    kind = _normalized(scope.get("kind")).replace(" ", "_")
    if kind not in SCOPE_KINDS:
        return None, "scope kind must be current_location, boroughs, nyc, or named_area"
    raw_values = scope.get("values")
    if raw_values is None:
        values: list[str] = []
    elif not isinstance(raw_values, list):
        return None, "scope values must be an array"
    else:
        values = [str(item).strip() for item in raw_values if str(item).strip()]

    if kind == "current_location":
        if values:
            return None, "current_location scope requires an empty values array"
        return {"kind": "current_location", "values": []}, None
    if kind == "nyc":
        if values and not all(
            _normalized(value) in _NYC_LOCALITY_WORDS for value in values
        ):
            return None, "nyc scope requires an empty values array"
        return {"kind": "nyc", "values": []}, None
    if kind == "named_area":
        if len(values) != 1:
            return None, "named_area scope requires exactly one value"
        mapped = canonical_borough(values[0])
        if mapped is not None:
            return {"kind": "boroughs", "values": [mapped]}, None
        if _normalized(values[0]) in {"nyc", "new york city", "new york"}:
            return {"kind": "nyc", "values": []}, None
        return {"kind": "named_area", "values": [values[0][:120]]}, None

    boroughs: list[str] = []
    seen: set[str] = set()
    for value in values:
        borough = canonical_borough(value)
        if borough is None:
            return None, f"unrecognized borough: {value}"
        if borough not in seen:
            boroughs.append(borough)
            seen.add(borough)
    if not boroughs:
        return None, "boroughs scope requires at least one canonical borough"
    return {"kind": "boroughs", "values": boroughs}, None


def borough_from_address_components(components: object) -> str | None:
    """Resolve a borough from Places address components, failing closed."""

    if not isinstance(components, list):
        return None
    sublocality = _component_text(components, "sublocality_level_1")
    sublocality_borough = canonical_borough(sublocality)
    if sublocality_borough is not None:
        return sublocality_borough
    locality = _component_text(components, "locality")
    locality_borough = canonical_borough(locality)
    if locality_borough is not None:
        return locality_borough
    return None


def borough_from_formatted_address(address: object) -> str | None:
    match = _UNAMBIGUOUS_ADDRESS_BOROUGH.search(str(address or ""))
    if match is None:
        return None
    return canonical_borough(match.group(1))


def resolve_place_borough(
    *,
    address_components: object = None,
    formatted_address: object = None,
    neighborhood: object = None,
) -> str | None:
    borough = borough_from_address_components(address_components)
    if borough is not None:
        return borough
    neighborhood_borough = canonical_borough(neighborhood)
    if neighborhood_borough is not None:
        return neighborhood_borough
    return borough_from_formatted_address(formatted_address)


def is_nyc_locality(address_components: object, formatted_address: object) -> bool:
    if borough_from_address_components(address_components):
        return True
    if borough_from_formatted_address(formatted_address):
        return True
    if not isinstance(address_components, list):
        return False
    locality = _normalized(_component_text(address_components, "locality"))
    admin1 = _normalized(_component_text(address_components, "administrative_area_level_1"))
    return locality in _NYC_LOCALITY_WORDS and admin1 in {"ny", "new york"}


def _component_text(components: list, wanted_type: str) -> str:
    for component in components:
        if not isinstance(component, dict):
            continue
        types = component.get("types") or []
        if wanted_type not in types:
            continue
        return str(
            component.get("longText")
            or component.get("long_name")
            or component.get("shortText")
            or component.get("short_name")
            or ""
        )
    return ""
