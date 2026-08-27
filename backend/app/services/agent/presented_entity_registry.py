"""Bounded memory for places the rider has actually seen.

The registry is deliberately separate from discovery-set storage. Discovery
sets are short-lived provider results; this registry is the session-level
reference surface that lets a rider refer back to an older visible place
after a later search. It stores only canonical display metadata and the
opaque id needed to reload the server-owned discovery record.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.services import cache

MAX_ENTRIES = 64
REGISTRY_FIELD = "presented_entity_registry"
_PRICE_REFERENCE_WORDS = frozenset({"cheap", "cheaper", "cheapest", "affordable", "budget"})
_BOROUGH_REFERENCE_WORDS = frozenset(
    {"brooklyn", "manhattan", "queens", "bronx", "staten island"}
)


def _store():
    # discovery_store owns the cache record and imports this module lazily;
    # keeping this import lazy avoids a module cycle while retaining one
    # canonical load/ownership check.
    from app.services.agent import discovery_store

    return discovery_store


def _identity_key(place: dict[str, Any]) -> str:
    return _store()._identity_key(place)


def _normalized_name(value: object) -> str:
    return _store()._normalized_name(value)


def _description_reference(value: str) -> str:
    words = " ".join(str(value or "").casefold().split()).split()
    if words and words[0] in {"the", "that"}:
        words = words[1:]
    if words and words[-1] in {"one", "place"}:
        words = words[:-1]
    return " ".join(words)


def _entries(session: dict | None) -> list[dict[str, Any]]:
    if not isinstance(session, dict):
        return []
    raw = session.get(REGISTRY_FIELD)
    if not isinstance(raw, list):
        session[REGISTRY_FIELD] = []
        return []
    now = time.time()
    entries: list[dict[str, Any]] = []
    for raw_entry in raw:
        if not isinstance(raw_entry, dict):
            continue
        try:
            expires_at = float(raw_entry.get("expires_at") or 0)
        except (TypeError, ValueError):
            continue
        if expires_at < now:
            continue
        place_id = str(raw_entry.get("place_id") or "").strip()
        set_id = str(raw_entry.get("discovery_set_id") or "").strip()
        identity = str(raw_entry.get("canonical_identity") or "").strip()
        if not place_id or not set_id or not identity:
            continue
        entry = dict(raw_entry)
        entry["place_id"] = place_id
        entry["canonical_place_id"] = str(
            raw_entry.get("canonical_place_id") or place_id
        ).strip()
        entry["discovery_set_id"] = set_id
        entry["canonical_identity"] = identity
        aliases = raw_entry.get("name_aliases")
        entry["name_aliases"] = (
            [str(alias).strip()[:120] for alias in aliases if str(alias).strip()][:4]
            if isinstance(aliases, list)
            else []
        )
        entries.append(entry)
    entries = entries[-MAX_ENTRIES:]
    session[REGISTRY_FIELD] = entries
    return entries


def snapshot(session: dict | None) -> list[dict[str, Any]]:
    """Return a copy suitable for context projection or persistence."""

    return [dict(entry) for entry in _entries(session)]


def clear(session: dict | None) -> None:
    if not isinstance(session, dict):
        return
    session[REGISTRY_FIELD] = []
    session.pop("presented_entity_sequence", None)


def place_ids(session: dict | None) -> dict[str, str]:
    return {
        str(entry.get("canonical_identity")): str(entry.get("place_id"))
        for entry in _entries(session)
        if entry.get("canonical_identity") and entry.get("place_id")
    }


def _next_sequence(session: dict) -> int:
    try:
        sequence = int(session.get("presented_entity_sequence") or 0) + 1
    except (TypeError, ValueError):
        sequence = 1
    session["presented_entity_sequence"] = sequence
    return sequence


def _entry(
    place: dict[str, Any],
    *,
    discovery_set_id: str,
    ordinal: int,
    sequence: int,
    presented_at: float,
    expires_at: float,
    reason: str,
    canonical_place_id: str | None = None,
    name_aliases: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "place_id": canonical_place_id or str(place.get("place_id") or ""),
        "canonical_place_id": canonical_place_id or str(place.get("place_id") or ""),
        "discovery_set_id": discovery_set_id,
        "ordinal": ordinal,
        "presentation_sequence": sequence,
        "presented_at": presented_at,
        "expires_at": expires_at,
        "canonical_identity": _identity_key(place),
        "reason": str(reason or "preference_match"),
        "name": str(place.get("name") or "")[:120],
        "name_aliases": [
            str(name).strip()[:120]
            for name in (name_aliases or [])
            if str(name).strip()
        ][:4],
        "address": str(place.get("address") or "")[:200],
        "neighborhood": str(place.get("neighborhood") or "")[:80],
        "borough": str(place.get("borough") or "")[:40],
        "category": str(place.get("category") or "")[:60],
    }


def record(
    session: dict | None,
    *,
    session_id: str,
    discovery_set_id: str,
    places: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Record only the places emitted by the canonical presenter."""

    if not isinstance(session, dict) or not session_id or not discovery_set_id:
        return list(places)
    store = _store()
    source = store.load_discovery_set(discovery_set_id, session_id=session_id)
    if source is None:
        return list(places)
    entries = _entries(session)
    by_identity = {
        str(entry.get("canonical_identity")): entry
        for entry in entries
        if entry.get("canonical_identity")
    }
    sequence = _next_sequence(session)
    presented_at = time.time()
    try:
        expires_at = float(source.get("expires_at") or presented_at)
    except (TypeError, ValueError):
        expires_at = presented_at
    canonical_places: list[dict[str, Any]] = []
    source_changed = False
    for ordinal, selected in enumerate(places, start=1):
        if not isinstance(selected, dict):
            continue
        identity = _identity_key(selected)
        existing = by_identity.get(identity)
        canonical_id = (
            str(existing.get("place_id") or "") if existing else ""
        ) or str(selected.get("place_id") or "")
        selected_id = str(selected.get("place_id") or "")
        if canonical_id and selected_id and canonical_id != selected_id:
            for stored in source.get("places") or []:
                if isinstance(stored, dict) and str(stored.get("place_id") or "") == selected_id:
                    stored["place_id"] = canonical_id
                    source_changed = True
                    break
        normalized = dict(selected)
        normalized["place_id"] = canonical_id
        canonical_places.append(normalized)
        item = _entry(
            normalized,
            discovery_set_id=discovery_set_id,
            ordinal=ordinal,
            sequence=sequence,
            presented_at=presented_at,
            expires_at=expires_at,
            reason=str(selected.get("reason") or "preference_match"),
            canonical_place_id=canonical_id,
            name_aliases=(
                [*list(existing.get("name_aliases") or []), str(existing.get("name") or "")]
                if existing
                else []
            ),
        )
        if existing is None:
            entries.append(item)
            by_identity[identity] = entries[-1]
        else:
            existing.clear()
            existing.update(item)
    session[REGISTRY_FIELD] = entries[-MAX_ENTRIES:]
    if source_changed:
        remaining_ttl = max(1, int(expires_at - time.time()))
        cache.cache_set(
            store._key(discovery_set_id),
            json.dumps(source, separators=(",", ":"), default=str),
            remaining_ttl,
            fail_open=True,
        )
    return canonical_places


def _entry_place(
    entry: dict[str, Any], *, session_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    set_id = str(entry.get("discovery_set_id") or "").strip()
    source = _store().load_discovery_set(set_id, session_id=session_id)
    if source is None:
        return None, None
    place_id = str(entry.get("place_id") or "").strip()
    identity = str(entry.get("canonical_identity") or "").strip()
    for place in source.get("places") or []:
        if not isinstance(place, dict):
            continue
        if place_id and str(place.get("place_id") or "") == place_id:
            return place, set_id
        if identity and _identity_key(place) == identity:
            return place, set_id
    return None, None


def resolve(
    *,
    session: dict | None,
    session_id: str,
    place_id: str | None = None,
    ordinal: int | None = None,
    description: str | None = None,
    discovery_set_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Resolve a presented name/id or newest compatible ordinal."""

    if not isinstance(session, dict) or not session_id:
        return None, None, None
    entries = _entries(session)
    requested_set = str(discovery_set_id or "").strip()
    if requested_set:
        entries = [
            item
            for item in entries
            if str(item.get("discovery_set_id") or "") == requested_set
        ]
    if not entries:
        return None, None, None

    chosen: dict[str, Any] | None = None
    if place_id:
        matches = [
            item for item in entries if str(item.get("place_id") or "") == str(place_id).strip()
        ]
        if matches:
            chosen = max(matches, key=lambda item: int(item.get("presentation_sequence") or 0))
    elif ordinal is not None:
        try:
            wanted = int(ordinal)
        except (TypeError, ValueError):
            return None, "ordinal must be a whole number", None
        sequences = sorted(
            {int(item.get("presentation_sequence") or 0) for item in entries},
            reverse=True,
        )
        for sequence in sequences:
            matches = [
                item
                for item in entries
                if int(item.get("presentation_sequence") or 0) == sequence
                and int(item.get("ordinal") or 0) == wanted
            ]
            if matches:
                chosen = matches[0]
                break
        if chosen is None:
            return None, "ordinal is out of range for presented places", None
    elif description is not None:
        reference = _description_reference(description)
        if not reference or reference in _PRICE_REFERENCE_WORDS or reference in _BOROUGH_REFERENCE_WORDS:
            return None, None, None
        normalized = _normalized_name(reference)
        matches = [
            item
            for item in entries
            if any(
                _normalized_name(name) == normalized
                or _normalized_name(name).startswith(normalized + " ")
                for name in [item.get("name"), *(item.get("name_aliases") or [])]
            )
        ]
        unique = {str(item.get("canonical_identity")): item for item in matches}
        if len(unique) > 1:
            return None, "multiple presented places match that name; please specify which one", None
        if unique:
            chosen = max(unique.values(), key=lambda item: int(item.get("presentation_sequence") or 0))

    if chosen is None:
        return None, None, None
    place, set_id = _entry_place(chosen, session_id=session_id)
    if place is None:
        return None, "the presented place is no longer available; search for it again", None
    return place, None, set_id


def _finite_price(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def resolve_description(
    places: list[dict[str, Any]], description: str
) -> tuple[dict[str, Any] | None, str | None]:
    words = " ".join(str(description or "").casefold().split()).split()
    if words and words[0] in {"the", "that"}:
        words = words[1:]
    if words and words[-1] in {"one", "place"}:
        words = words[:-1]
    reference = " ".join(words)
    if not reference:
        return None, "place reference is incomplete"
    if reference in _PRICE_REFERENCE_WORDS:
        known = [
            (place, _finite_price(place.get("price_level")))
            for place in places
            if _finite_price(place.get("price_level")) is not None
        ]
        if not known:
            return None, "price information is unavailable for these places"
        lowest = min(price for _place, price in known)
        matches = [place for place, price in known if price == lowest]
        return (
            (matches[0], None)
            if len(matches) == 1
            else (None, "multiple places match that price reference")
        )
    if reference in _BOROUGH_REFERENCE_WORDS:
        matches = [
            place
            for place in places
            if reference in " ".join(
                str(value or "").casefold()
                for value in (place.get("neighborhood"), place.get("address"))
            )
        ]
        return (
            (matches[0], None)
            if len(matches) == 1
            else (
                None,
                "multiple places match that location reference"
                if len(matches) > 1
                else "no place matches that location reference",
            )
        )
    reference_text = " ".join(reference.split())
    matches = [
        place
        for place in places
        if reference_text in " ".join(
            str(place.get(field) or "").casefold()
            for field in ("name", "category")
        )
    ]
    return (
        (matches[0], None)
        if len(matches) == 1
        else (
            None,
            "multiple places match that description"
            if len(matches) > 1
            else "no place matches that description",
        )
    )
