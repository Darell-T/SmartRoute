"""Strict, offline fixture loading for route-intelligence replays.

Replays keep provider payloads close to the form received from each provider:
GTFS-RT payloads are base64-encoded protobuf bytes, 511NY is its event-array
response, and Ticketmaster is its Discovery API response.  The adapters below
call the production parsing/normalization functions instead of manufacturing
their final forms.

This module deliberately stops before route selection.  The comparison runner
owns baseline/intelligence decisions and consumes :class:`ReplayInputs`.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import socket
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping
from unittest.mock import patch

import httpx
import requests
from app.services.agent.tools import event_lookup
from app.services.mta import alerts as mta_alerts
from app.services.mta import bus as mta_bus
from app.services.mta import subway as mta_subway
from app.services.ny511 import NY511Settings, SnapshotStore
from app.services.trips.incident_context import extract_candidate_stop_context
from app.services.trips.incident_matching import match_cached_incidents
from app.services.trips.incident_merge import merge_incident_evidence
from app.services.trips.incidents import _normalize_advisor_incident
from app.services.trips import advisor_context, candidates


SCENARIO_FILENAME = "scenario.json"
FIXTURE_KEYS = frozenset(
    {
        "route_candidates",
        "mta_alerts",
        "subway_vehicle_positions",
        "bus_vehicle_positions",
        "stalled_vehicle_evidence",
        "grok_x",
        "grok_web",
        "ny511",
        "ticketmaster",
        "advisor_outputs",
    }
)
SOURCE_NAMES = frozenset(
    {
        "mta",
        "vehicle_detection",
        "grok_x",
        "grok_web",
        "511ny",
        "ticketmaster",
    }
)


class ScenarioValidationError(ValueError):
    """A replay fixture is absent, malformed, or not safe to load."""


def _default_replay_root() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "replays"


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ScenarioValidationError(f"{label} must be an object")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _parse_frozen_time(value: object) -> datetime:
    raw = _require_string(value, "frozen_time")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScenarioValidationError("frozen_time must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ScenarioValidationError("frozen_time must include a UTC offset")
    return parsed.astimezone(UTC)


def _safe_fixture_path(root: Path, relative_path: object, key: str) -> Path:
    relative = Path(_require_string(relative_path, f"fixtures.{key}"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ScenarioValidationError(f"fixtures.{key} must remain inside its scenario directory")
    resolved_root = root.resolve()
    candidate = (root / relative).resolve()
    if resolved_root not in candidate.parents or not candidate.is_file():
        raise ScenarioValidationError(f"fixture does not exist: {relative}")
    return candidate


@dataclass(frozen=True)
class FrozenClock:
    """An explicit, timezone-aware time source for replay consumers."""

    current: datetime

    def __post_init__(self) -> None:
        if self.current.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")

    def now(self) -> datetime:
        return self.current

    def timestamp(self) -> float:
        return self.current.timestamp()


@dataclass(frozen=True)
class ReplayScenario:
    scenario_id: str
    description: str
    frozen_time: datetime
    origin: Mapping[str, Any]
    destination: Mapping[str, Any]
    enabled_sources: frozenset[str]
    expected: Mapping[str, Any]
    fixture_paths: Mapping[str, Path]
    root: Path

    @property
    def clock(self) -> FrozenClock:
        return FrozenClock(self.frozen_time)

    def read_json_fixture(self, key: str) -> object:
        path = self.fixture_paths.get(key)
        if path is None:
            raise ScenarioValidationError(f"scenario has no {key} fixture")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ScenarioValidationError(f"invalid JSON fixture: {path.name}") from exc

    def read_base64_fixture(self, key: str) -> bytes:
        path = self.fixture_paths.get(key)
        if path is None:
            raise ScenarioValidationError(f"scenario has no {key} fixture")
        try:
            encoded = "".join(path.read_text(encoding="ascii").split())
            return base64.b64decode(encoded, validate=True)
        except (OSError, UnicodeDecodeError, ValueError, binascii.Error) as exc:
            raise ScenarioValidationError(f"invalid base64 fixture: {path.name}") from exc


def load_scenario(scenario: str | Path, *, replay_root: Path | None = None) -> ReplayScenario:
    """Load one strict replay manifest without touching the network."""
    root = Path(scenario)
    if not root.exists():
        root = (replay_root or _default_replay_root()) / str(scenario)
    manifest_path = root / SCENARIO_FILENAME
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScenarioValidationError(f"invalid or missing {SCENARIO_FILENAME}: {manifest_path}") from exc
    manifest = _require_mapping(raw, SCENARIO_FILENAME)
    required = {"scenario_id", "description", "frozen_time", "origin", "destination", "enabled_sources", "fixtures", "expected"}
    unknown = set(manifest) - required
    missing = required - set(manifest)
    if unknown or missing:
        raise ScenarioValidationError(
            f"scenario manifest keys are invalid (missing={sorted(missing)}, unknown={sorted(unknown)})"
        )
    enabled = manifest["enabled_sources"]
    if not isinstance(enabled, list) or not enabled or any(not isinstance(item, str) for item in enabled):
        raise ScenarioValidationError("enabled_sources must be a non-empty string list")
    enabled_set = frozenset(enabled)
    if not enabled_set <= SOURCE_NAMES:
        raise ScenarioValidationError(f"unknown enabled sources: {sorted(enabled_set - SOURCE_NAMES)}")
    fixtures = _require_mapping(manifest["fixtures"], "fixtures")
    fixture_unknown = set(fixtures) - FIXTURE_KEYS
    fixture_missing = FIXTURE_KEYS - set(fixtures)
    if fixture_unknown or fixture_missing:
        raise ScenarioValidationError(
            f"fixture keys are invalid (missing={sorted(fixture_missing)}, unknown={sorted(fixture_unknown)})"
        )
    # Reject unsafe paths before checking file existence so a malformed
    # manifest cannot hide traversal behind an unrelated missing fixture.
    for key in FIXTURE_KEYS:
        relative = Path(_require_string(fixtures[key], f"fixtures.{key}"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ScenarioValidationError(f"fixtures.{key} must remain inside its scenario directory")
    fixture_paths = {key: _safe_fixture_path(root, fixtures[key], key) for key in FIXTURE_KEYS}
    return ReplayScenario(
        scenario_id=_require_string(manifest["scenario_id"], "scenario_id"),
        description=_require_string(manifest["description"], "description"),
        frozen_time=_parse_frozen_time(manifest["frozen_time"]),
        origin=_require_mapping(manifest["origin"], "origin"),
        destination=_require_mapping(manifest["destination"], "destination"),
        enabled_sources=enabled_set,
        expected=_require_mapping(manifest["expected"], "expected"),
        fixture_paths=fixture_paths,
        root=root.resolve(),
    )


def load_all_scenarios(*, replay_root: Path | None = None) -> list[ReplayScenario]:
    root = replay_root or _default_replay_root()
    if not root.is_dir():
        raise ScenarioValidationError(f"replay root does not exist: {root}")
    manifests = sorted(root.glob(f"*/{SCENARIO_FILENAME}"))
    if not manifests:
        raise ScenarioValidationError(f"no replay scenarios found in {root}")
    scenarios = [load_scenario(path.parent) for path in manifests]
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ScenarioValidationError("scenario_id values must be unique")
    return scenarios


def _network_blocked(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("network access is disabled during deterministic replay")


async def _async_network_blocked(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("network access is disabled during deterministic replay")


@contextmanager
def network_disabled() -> Iterator[None]:
    """Prevent accidental outbound network access in deterministic replays."""
    with patch.object(socket, "create_connection", _network_blocked), patch.object(
        socket.socket, "connect", _network_blocked
    ), patch.object(httpx.AsyncClient, "request", _async_network_blocked), patch.object(
        httpx.Client, "request", _network_blocked
    ), patch.object(requests.sessions.Session, "request", _network_blocked):
        yield


@dataclass(frozen=True)
class ReplayInputs:
    """Production-normalized provider inputs made available to later runners."""

    route_candidates: list[list[dict[str, Any]]]
    mta_alerts: list[dict[str, Any]]
    subway_vehicle_positions: list[dict[str, Any]]
    bus_vehicle_positions: list[dict[str, Any]]
    stalled_trains: list[dict[str, Any]]
    stalled_buses: list[dict[str, Any]]
    grok_incidents: list[dict[str, Any]]
    ny511_snapshot: Any
    ny511_matches: list[dict[str, Any]]
    ticketmaster_events: list[dict[str, Any]]
    # Recorded, sanitized advisor output for each planning mode.  The replay
    # foundation validates but deliberately does not parse or select from it.
    advisor_outputs: Mapping[str, str]


class ReplayFixtureAdapters:
    """Load recorded provider fixtures through production helper functions."""

    def __init__(self, scenario: ReplayScenario) -> None:
        self.scenario = scenario

    @staticmethod
    def _route_candidates(value: object) -> list[list[dict[str, Any]]]:
        if not isinstance(value, list) or not value or any(not isinstance(route, list) for route in value):
            raise ScenarioValidationError("route_candidates must be a non-empty list of routes")
        result: list[list[dict[str, Any]]] = []
        for route in value:
            if any(not isinstance(step, dict) for step in route):
                raise ScenarioValidationError("route candidates must contain route-step objects")
            result.append([dict(step) for step in route])
        return result

    @staticmethod
    def _list_of_objects(value: object, label: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ScenarioValidationError(f"{label} must be a list of objects")
        return [dict(item) for item in value]

    def _advisor_outputs(self, candidate_count: int) -> Mapping[str, str]:
        value = _require_mapping(self.scenario.read_json_fixture("advisor_outputs"), "advisor_outputs")
        expected = {"baseline", "intelligence"}
        if set(value) != expected:
            raise ScenarioValidationError("advisor_outputs must contain exactly baseline and intelligence")
        outputs = {key: _require_string(value[key], f"advisor_outputs.{key}") for key in expected}
        for mode, transcript in outputs.items():
            route_match = re.search(r"\[ROUTE:(\d+)\]", transcript)
            analysis_selected, analysis = candidates._parse_candidate_analysis(transcript)
            # Use the same endpoint parser that production uses, while also
            # rejecting the endpoint's deliberate route-zero fallback. A
            # replay transcript must prove a real selection contract.
            selected, _parsed_analysis = advisor_context.parse_advisor_selection(transcript, candidate_count)
            explicit_selection = int(route_match.group(1)) if route_match else analysis_selected
            if explicit_selection is None or not 0 <= explicit_selection < candidate_count:
                raise ScenarioValidationError(f"advisor_outputs.{mode} has no valid candidate selection")
            if selected != explicit_selection:
                raise ScenarioValidationError(f"advisor_outputs.{mode} falls back outside its recorded selection")
            if set(analysis) != set(range(candidate_count)):
                raise ScenarioValidationError(
                    f"advisor_outputs.{mode} candidate analysis must cover every candidate"
                )
        return outputs

    async def load(self) -> ReplayInputs:
        """Normalize one recorded scenario while enforcing offline execution."""
        with network_disabled():
            return await self._load_offline()

    async def _load_offline(self) -> ReplayInputs:
        """Normalize one recorded scenario through production pure helpers."""
        routes = self._route_candidates(self.scenario.read_json_fixture("route_candidates"))
        clock = self.scenario.clock
        alert_bytes = self.scenario.read_base64_fixture("mta_alerts")
        vehicle_bytes = self.scenario.read_base64_fixture("subway_vehicle_positions")
        try:
            parsed_alerts = mta_alerts._parse_service_alerts(
                alert_bytes, include_same_day=False, now_timestamp=clock.timestamp()
            )
            parsed_vehicles = mta_subway.parse_vehicle_positions(
                vehicle_bytes, source="replay", include_stop_only=True
            )
        except Exception as exc:
            raise ScenarioValidationError("invalid GTFS-RT provider fixture") from exc

        bus_payload = self.scenario.read_json_fixture("bus_vehicle_positions")
        if not isinstance(bus_payload, dict):
            raise ScenarioValidationError("bus_vehicle_positions must be a SIRI provider object")
        bus_positions = mta_bus.parse_stalled_bus_positions(bus_payload)
        candidate_route_ids = {
            str(step.get("route_id") or step.get("train_line") or "").strip().upper()
            for route in routes for step in route
            if isinstance(step, dict) and step.get("type") in {"SUBWAY", "BUS"}
        }
        candidate_route_ids.discard("")
        stalled_trains = mta_subway.detect_stalled_trains(
            parsed_vehicles, candidate_route_ids, now_timestamp=clock.timestamp()
        )
        expected_stalled = _require_mapping(
            self.scenario.read_json_fixture("stalled_vehicle_evidence"), "stalled_vehicle_evidence"
        )
        if set(expected_stalled) != {"stalled_trains", "stalled_buses"}:
            raise ScenarioValidationError("stalled_vehicle_evidence must contain stalled_trains and stalled_buses")
        expected_trains = self._list_of_objects(expected_stalled["stalled_trains"], "stalled_vehicle_evidence.stalled_trains")
        expected_buses = self._list_of_objects(expected_stalled["stalled_buses"], "stalled_vehicle_evidence.stalled_buses")
        if expected_trains != stalled_trains or expected_buses != bus_positions:
            raise ScenarioValidationError("stalled_vehicle_evidence does not match production-derived signals")
        grok_rows = self._list_of_objects(self.scenario.read_json_fixture("grok_x"), "grok_x")
        grok_rows.extend(self._list_of_objects(self.scenario.read_json_fixture("grok_web"), "grok_web"))
        # Grok's final structured incident rows flow through the production
        # conservative merger and the advisor-facing contract sanitizer.
        grok_incidents = [_normalize_advisor_incident(row) for row in merge_incident_evidence(grok_rows, now=clock.now())]

        ny511_records = self.scenario.read_json_fixture("ny511")
        if not isinstance(ny511_records, list):
            raise ScenarioValidationError("ny511 must be a provider event array")
        settings = NY511Settings(api_key=None, enabled=False)
        store = SnapshotStore(settings)
        try:
            await store.record_success(ny511_records, fetched_at=clock.now())
            snapshot = await store.get_snapshot(now=clock.now())
        except Exception as exc:
            raise ScenarioValidationError("invalid 511NY provider fixture") from exc
        stops = extract_candidate_stop_context(routes)
        matches = match_cached_incidents(snapshot.incidents, stops)

        ticketmaster_payload = self.scenario.read_json_fixture("ticketmaster")
        raw_events, _pages = event_lookup._events_from_payload(ticketmaster_payload)
        if raw_events is None:
            raise ScenarioValidationError("invalid Ticketmaster provider fixture")
        ticketmaster_events = [event_lookup._parse_event(event) for event in raw_events]
        advisor_outputs = self._advisor_outputs(len(routes))

        return ReplayInputs(
            route_candidates=routes,
            mta_alerts=parsed_alerts,
            subway_vehicle_positions=parsed_vehicles,
            bus_vehicle_positions=bus_positions,
            stalled_trains=stalled_trains,
            stalled_buses=bus_positions,
            grok_incidents=grok_incidents,
            ny511_snapshot=snapshot,
            ny511_matches=[match.as_dict() for match in matches],
            ticketmaster_events=ticketmaster_events,
            advisor_outputs=advisor_outputs,
        )
