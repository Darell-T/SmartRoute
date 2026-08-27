"""Strict, offline fixture loading for route-intelligence evaluation replays.

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
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn
from unittest.mock import patch

import httpx
from app.services.incidents.ny511 import NY511Settings, SnapshotStore
from app.services.mta import alerts as mta_alerts
from app.services.mta import bus as mta_bus
from app.services.mta import subway as mta_subway
from app.services.trips.crowds import event_provider
from app.services.trips.route_incidents.context import extract_candidate_stop_context
from app.services.trips.route_incidents.matching import match_cached_incidents

from evaluation.route_intelligence import advisor_context

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
        # ``vehicle_detection`` is retained for existing scenarios.  New
        # manifests can control the two production-derived detectors
        # independently, which is essential for meaningful source ablations.
        "vehicle_detection",
        "subway_vehicle_detection",
        "bus_vehicle_detection",
        "grok_x",
        "grok_web",
        "511ny",
        "ticketmaster",
    }
)
CANONICAL_SOURCE_NAMES = frozenset(
    {
        "mta",
        "subway_vehicle_detection",
        "bus_vehicle_detection",
        "grok_x",
        "grok_web",
        "511ny",
        "ticketmaster",
    }
)
SOURCE_STATUS_VALUES = frozenset({"complete", "partial", "failed", "disabled"})
_MAX_SOURCE_ERRORS = 8
_MAX_SOURCE_ERROR_LENGTH = 160


class ScenarioValidationError(ValueError):
    """A replay fixture is absent, malformed, or not safe to load."""


def _invalid(message: str) -> NoReturn:
    raise ScenarioValidationError(message)


@dataclass(frozen=True)
class SourceStatus:
    """Bounded replay metadata for one provider or detector.

    The status describes whether a fixture's source is available, not whether
    its evidence is relevant.  A complete source may legitimately return an
    empty collection.  Error text is deliberately short and sanitized because
    scenario diagnostics are included in machine-readable reports.
    """

    status: str
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {"status": self.status, "errors": list(self.errors)}


def _default_replay_root() -> Path:
    return Path(__file__).resolve().parent / "replays"


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _invalid(f"{label} must be an object")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(f"{label} must be a non-empty string")
    return value.strip()


_CREDENTIAL_ERROR_PATTERN = (
    r"\b(?:authorization|bearer|api[_-]?key|apikey|token|secret|password)\b"
    r"(?:\s*[:=]\s*|\s+)[^\s,;]+|\bsk-[a-z0-9_-]+|[?&](?:api[_-]?key|apikey|token|secret|password)="
)
_SOURCE_ERROR_CATEGORIES = (
    (("timeout", "timed out"), "timeout"),
    (("malformed", "invalid json", "invalid response"), "malformed_response"),
    (("stale", "expired"), "stale"),
    (("disabled", "not configured"), "disabled"),
    (("unavailable",), "unavailable"),
    (("limit",), "limit_reached"),
)


def _source_error_category(normalized: str) -> str:
    if re.search(_CREDENTIAL_ERROR_PATTERN, normalized, flags=re.IGNORECASE):
        return "auth_error"
    for needles, label in _SOURCE_ERROR_CATEGORIES:
        if any(needle in normalized for needle in needles):
            return label
    return "source_error"


def _safe_source_error(value: object, label: str) -> str:
    """Map authored/provider diagnostics to stable, non-sensitive categories.

    Replay reports are durable artifacts. They must not echo arbitrary fixture
    wording because it may contain a provider credential, a user address, or
    other data that is irrelevant to source availability.
    """

    error = _require_string(value, label)
    if len(error) > _MAX_SOURCE_ERROR_LENGTH:
        _invalid(f"{label} must be at most {_MAX_SOURCE_ERROR_LENGTH} characters")
    return _source_error_category(error.casefold())


def _parse_one_source_status(source: object, raw_entry: object) -> tuple[str, SourceStatus]:
    if not isinstance(source, str) or source not in SOURCE_NAMES:
        _invalid(f"source_status contains unknown source: {source!r}")
    entry = _require_mapping(raw_entry, f"source_status.{source}")
    if set(entry) != {"status", "errors"}:
        _invalid(f"source_status.{source} must contain exactly status and errors")
    status = entry["status"]
    if not isinstance(status, str) or status not in SOURCE_STATUS_VALUES:
        _invalid(
            f"source_status.{source}.status must be one of {sorted(SOURCE_STATUS_VALUES)}"
        )
    errors = entry["errors"]
    if not isinstance(errors, list) or len(errors) > _MAX_SOURCE_ERRORS:
        _invalid(
            f"source_status.{source}.errors must be a list of at most {_MAX_SOURCE_ERRORS} strings"
        )
    sanitized_errors = tuple(
        _safe_source_error(item, f"source_status.{source}.errors[{index}]")
        for index, item in enumerate(errors)
    )
    if status in {"complete", "disabled"} and sanitized_errors:
        _invalid(f"source_status.{source}.{status} must not include errors")
    return source, SourceStatus(status=status, errors=sanitized_errors)


def _parse_source_statuses(value: object | None) -> Mapping[str, SourceStatus]:
    """Validate optional, provider-shaped replay status metadata.

    Omitted metadata stays backwards compatible: each enabled source is
    treated as complete and each disabled source as disabled.  Explicit
    metadata is strict so a malformed failure fixture cannot accidentally
    become a false all-clear.
    """

    if value is None:
        return {}
    raw = _require_mapping(value, "source_status")
    return dict(_parse_one_source_status(source, raw_entry) for source, raw_entry in raw.items())


def canonical_sources(enabled_sources: Iterable[str]) -> frozenset[str]:
    """Expand the legacy combined vehicle toggle to the seven real sources."""

    enabled = set(enabled_sources)
    canonical = enabled & CANONICAL_SOURCE_NAMES
    if "vehicle_detection" in enabled:
        canonical.update({"subway_vehicle_detection", "bus_vehicle_detection"})
    return frozenset(canonical)


def source_status_for(
    scenario: ReplayScenario, enabled_sources: Iterable[str], source: str
) -> SourceStatus:
    """Return an effective status after an optional ablation override.

    A caller disabling a source always wins over its recorded fixture status.
    A legacy ``vehicle_detection`` status is inherited by its two independently
    controllable detector sources unless a specific status is supplied.
    """

    if source not in CANONICAL_SOURCE_NAMES:
        _invalid(f"unknown canonical source: {source}")
    if source not in canonical_sources(enabled_sources):
        return SourceStatus("disabled")
    recorded = scenario.source_status.get(source)
    if recorded is None and source in {"subway_vehicle_detection", "bus_vehicle_detection"}:
        recorded = scenario.source_status.get("vehicle_detection")
    return recorded or SourceStatus("complete")


def _parse_aware_time(value: object, label: str) -> datetime:
    raw = _require_string(value, label)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        iso_message = f"{label} must be ISO-8601"
        raise ScenarioValidationError(iso_message) from exc
    if parsed.tzinfo is None:
        _invalid(f"{label} must include a UTC offset")
    return parsed.astimezone(UTC)


def _parse_frozen_time(value: object) -> datetime:
    return _parse_aware_time(value, "frozen_time")


def _safe_fixture_path(root: Path, relative_path: object, key: str) -> Path:
    relative = Path(_require_string(relative_path, f"fixtures.{key}"))
    if relative.is_absolute() or ".." in relative.parts:
        _invalid(f"fixtures.{key} must remain inside its scenario directory")
    resolved_root = root.resolve()
    candidate = (root / relative).resolve()
    if resolved_root not in candidate.parents or not candidate.is_file():
        _invalid(f"fixture does not exist: {relative}")
    return candidate


@dataclass(frozen=True)
class FrozenClock:
    """An explicit, timezone-aware time source for replay consumers."""

    current: datetime

    def __post_init__(self) -> None:
        if self.current.tzinfo is None:
            timezone_required = "FrozenClock requires a timezone-aware datetime"
            raise ValueError(timezone_required)

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
    source_status: Mapping[str, SourceStatus]
    ny511_snapshot_fetched_at: datetime | None
    expected: Mapping[str, Any]
    fixture_paths: Mapping[str, Path]
    root: Path

    @property
    def clock(self) -> FrozenClock:
        return FrozenClock(self.frozen_time)

    def read_json_fixture(self, key: str) -> object:
        path = self.fixture_paths.get(key)
        if path is None:
            _invalid(f"scenario has no {key} fixture")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            invalid_json = f"invalid JSON fixture: {path.name}"
            raise ScenarioValidationError(invalid_json) from exc

    def read_base64_fixture(self, key: str) -> bytes:
        path = self.fixture_paths.get(key)
        if path is None:
            _invalid(f"scenario has no {key} fixture")
        try:
            encoded = "".join(path.read_text(encoding="ascii").split())
            return base64.b64decode(encoded, validate=True)
        except (OSError, UnicodeDecodeError, ValueError, binascii.Error) as exc:
            invalid_base64 = f"invalid base64 fixture: {path.name}"
            raise ScenarioValidationError(invalid_base64) from exc


def _read_scenario_manifest(root: Path) -> Mapping[str, Any]:
    manifest_path = root / SCENARIO_FILENAME
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        missing_manifest = f"invalid or missing {SCENARIO_FILENAME}: {manifest_path}"
        raise ScenarioValidationError(missing_manifest) from exc
    return _require_mapping(raw, SCENARIO_FILENAME)


def _require_manifest_keys(manifest: Mapping[str, Any]) -> None:
    required = {
        "scenario_id",
        "description",
        "frozen_time",
        "origin",
        "destination",
        "enabled_sources",
        "fixtures",
        "expected",
    }
    allowed = required | {"source_status", "ny511_snapshot_fetched_at"}
    unknown = set(manifest) - allowed
    missing = required - set(manifest)
    if unknown or missing:
        _invalid(
            f"scenario manifest keys are invalid (missing={sorted(missing)}, unknown={sorted(unknown)})"
        )


def _parse_enabled_sources(manifest: Mapping[str, Any]) -> frozenset[str]:
    enabled = manifest["enabled_sources"]
    if not isinstance(enabled, list) or not enabled or any(not isinstance(item, str) for item in enabled):
        _invalid("enabled_sources must be a non-empty string list")
    enabled_set = frozenset(enabled)
    if not enabled_set <= SOURCE_NAMES:
        _invalid(f"unknown enabled sources: {sorted(enabled_set - SOURCE_NAMES)}")
    return enabled_set


def _parse_fixture_paths(root: Path, manifest: Mapping[str, Any]) -> dict[str, Path]:
    fixtures = _require_mapping(manifest["fixtures"], "fixtures")
    fixture_unknown = set(fixtures) - FIXTURE_KEYS
    fixture_missing = FIXTURE_KEYS - set(fixtures)
    if fixture_unknown or fixture_missing:
        _invalid(
            f"fixture keys are invalid (missing={sorted(fixture_missing)}, unknown={sorted(fixture_unknown)})"
        )
    # Reject unsafe paths before checking file existence so a malformed
    # manifest cannot hide traversal behind an unrelated missing fixture.
    for key in FIXTURE_KEYS:
        relative = Path(_require_string(fixtures[key], f"fixtures.{key}"))
        if relative.is_absolute() or ".." in relative.parts:
            _invalid(f"fixtures.{key} must remain inside its scenario directory")
    return {key: _safe_fixture_path(root, fixtures[key], key) for key in FIXTURE_KEYS}


def _optional_ny511_fetched_at(manifest: Mapping[str, Any]) -> datetime | None:
    if manifest.get("ny511_snapshot_fetched_at") is None:
        return None
    return _parse_aware_time(manifest["ny511_snapshot_fetched_at"], "ny511_snapshot_fetched_at")


def load_scenario(scenario: str | Path, *, replay_root: Path | None = None) -> ReplayScenario:
    """Load one strict replay manifest without touching the network."""
    root = Path(scenario)
    if not root.exists():
        root = (replay_root or _default_replay_root()) / str(scenario)
    manifest = _read_scenario_manifest(root)
    _require_manifest_keys(manifest)
    enabled_set = _parse_enabled_sources(manifest)
    fixture_paths = _parse_fixture_paths(root, manifest)
    return ReplayScenario(
        scenario_id=_require_string(manifest["scenario_id"], "scenario_id"),
        description=_require_string(manifest["description"], "description"),
        frozen_time=_parse_frozen_time(manifest["frozen_time"]),
        origin=_require_mapping(manifest["origin"], "origin"),
        destination=_require_mapping(manifest["destination"], "destination"),
        enabled_sources=enabled_set,
        source_status=_parse_source_statuses(manifest.get("source_status")),
        ny511_snapshot_fetched_at=_optional_ny511_fetched_at(manifest),
        expected=_require_mapping(manifest["expected"], "expected"),
        fixture_paths=fixture_paths,
        root=root.resolve(),
    )


def load_all_scenarios(*, replay_root: Path | None = None) -> list[ReplayScenario]:
    root = replay_root or _default_replay_root()
    if not root.is_dir():
        _invalid(f"replay root does not exist: {root}")
    manifests = sorted(root.glob(f"*/{SCENARIO_FILENAME}"))
    if not manifests:
        _invalid(f"no replay scenarios found in {root}")
    scenarios = [load_scenario(path.parent) for path in manifests]
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        _invalid("scenario_id values must be unique")
    return scenarios


_NETWORK_DISABLED = "network access is disabled during deterministic replay"


def _network_blocked(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError(_NETWORK_DISABLED)


async def _async_network_blocked(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError(_NETWORK_DISABLED)


@contextmanager
def network_disabled() -> Iterator[None]:
    """Prevent accidental outbound network access in deterministic replays."""
    with (
        patch.object(socket, "create_connection", _network_blocked),
        patch.object(socket.socket, "connect", _network_blocked),
        patch.object(httpx.AsyncClient, "request", _async_network_blocked),  # noqa: TID251 replay patches httpx to disable requests
        patch.object(httpx.Client, "request", _network_blocked),  # noqa: TID251 replay patches httpx to disable requests
    ):
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
    # A scenario may record the deterministic advisor result after disabling
    # exactly one canonical intelligence source.  These are not synthetic
    # scores: they are the same strict parser contract as the primary outputs.
    advisor_ablation_outputs: Mapping[str, str]


class ReplayFixtureAdapters:
    """Load recorded provider fixtures through production helper functions."""

    def __init__(self, scenario: ReplayScenario) -> None:
        self.scenario = scenario

    @staticmethod
    def _route_candidates(value: object) -> list[list[dict[str, Any]]]:
        if not isinstance(value, list) or not value or any(not isinstance(route, list) for route in value):
            _invalid("route_candidates must be a non-empty list of routes")
        result: list[list[dict[str, Any]]] = []
        for route in value:
            if any(not isinstance(step, dict) for step in route):
                _invalid("route candidates must contain route-step objects")
            result.append([dict(step) for step in route])
        return result

    @staticmethod
    def _list_of_objects(value: object, label: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            _invalid(f"{label} must be a list of objects")
        return [dict(item) for item in value]

    @staticmethod
    def _recorded_selection(transcript: str, label: str, candidate_count: int) -> None:
        """Reject fallback-shaped recordings before a comparison can use them."""

        route_match = re.search(r"\[ROUTE:(\d+)\]", transcript)
        analysis_selected, analysis = advisor_context.parse_candidate_analysis(transcript)
        # Use the same endpoint parser that production uses, while also
        # rejecting the endpoint's deliberate route-zero fallback. A replay
        # transcript must prove a real selection contract.
        selected, _parsed_analysis = advisor_context.parse_advisor_selection(transcript, candidate_count)
        explicit_selection = int(route_match.group(1)) if route_match else analysis_selected
        if explicit_selection is None or not 0 <= explicit_selection < candidate_count:
            _invalid(f"{label} has no valid candidate selection")
        if selected != explicit_selection:
            _invalid(f"{label} falls back outside its recorded selection")
        if set(analysis) != set(range(candidate_count)):
            _invalid(
                f"{label} candidate analysis must cover every candidate"
            )

    def _advisor_outputs(self, candidate_count: int) -> tuple[Mapping[str, str], Mapping[str, str]]:
        value = _require_mapping(self.scenario.read_json_fixture("advisor_outputs"), "advisor_outputs")
        expected = {"baseline", "intelligence"}
        if set(value) - (expected | {"ablations"}) or expected - set(value):
            _invalid(
                "advisor_outputs must contain baseline, intelligence, and optional ablations"
            )
        outputs = {key: _require_string(value[key], f"advisor_outputs.{key}") for key in expected}
        for mode, transcript in outputs.items():
            self._recorded_selection(transcript, f"advisor_outputs.{mode}", candidate_count)

        raw_ablations = value.get("ablations", {})
        if not isinstance(raw_ablations, Mapping):
            _invalid("advisor_outputs.ablations must be an object")
        ablations: dict[str, str] = {}
        for source, raw_transcript in raw_ablations.items():
            if not isinstance(source, str) or source not in CANONICAL_SOURCE_NAMES:
                _invalid(
                    f"advisor_outputs.ablations contains unknown source: {source!r}"
                )
            transcript = _require_string(raw_transcript, f"advisor_outputs.ablations.{source}")
            self._recorded_selection(
                transcript, f"advisor_outputs.ablations.{source}", candidate_count
            )
            ablations[source] = transcript
        return outputs, ablations

    def _parse_gtfs_replay_fixtures(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        clock = self.scenario.clock
        alert_bytes = self.scenario.read_base64_fixture("mta_alerts")
        vehicle_bytes = self.scenario.read_base64_fixture("subway_vehicle_positions")
        try:
            parsed_alerts = mta_alerts._parse_service_alerts(
                alert_bytes,
                include_same_day=False,
                now_timestamp=clock.timestamp(),
            )
            parsed_vehicles = mta_subway.parse_vehicle_positions(
                vehicle_bytes,
                source="replay",
                include_stop_only=True,
            )
        except Exception as exc:
            invalid_gtfs = "invalid GTFS-RT provider fixture"
            raise ScenarioValidationError(invalid_gtfs) from exc
        return parsed_alerts, parsed_vehicles

    def _require_stalled_vehicle_evidence(
        self,
        stalled_trains: list[dict[str, Any]],
        bus_positions: list[dict[str, Any]],
    ) -> None:
        expected_stalled = _require_mapping(
            self.scenario.read_json_fixture("stalled_vehicle_evidence"),
            "stalled_vehicle_evidence",
        )
        if set(expected_stalled) != {"stalled_trains", "stalled_buses"}:
            _invalid("stalled_vehicle_evidence must contain stalled_trains and stalled_buses")
        expected_trains = self._list_of_objects(
            expected_stalled["stalled_trains"], "stalled_vehicle_evidence.stalled_trains"
        )
        expected_buses = self._list_of_objects(
            expected_stalled["stalled_buses"], "stalled_vehicle_evidence.stalled_buses"
        )
        if expected_trains != stalled_trains or expected_buses != bus_positions:
            _invalid("stalled_vehicle_evidence does not match production-derived signals")

    async def _load_ny511_replay(self, clock: FrozenClock) -> tuple[Any, list[Any]]:
        ny511_records = self.scenario.read_json_fixture("ny511")
        if not isinstance(ny511_records, list):
            _invalid("ny511 must be a provider event array")
        store = SnapshotStore(NY511Settings(api_key=None, enabled=False))
        try:
            await store.record_success(
                ny511_records,
                fetched_at=self.scenario.ny511_snapshot_fetched_at or clock.now(),
            )
            snapshot = await store.get_snapshot(now=clock.now())
        except Exception as exc:
            invalid_511 = "invalid 511NY provider fixture"
            raise ScenarioValidationError(invalid_511) from exc
        return snapshot, snapshot.incidents

    def _load_ticketmaster_events(self) -> list[Any]:
        raw_events, _pages = event_provider._events_from_payload(
            self.scenario.read_json_fixture("ticketmaster")
        )
        if raw_events is None:
            _invalid("invalid Ticketmaster provider fixture")
        return [event_provider._parse_event(event) for event in raw_events]

    async def load(self) -> ReplayInputs:
        """Normalize one recorded scenario while enforcing offline execution."""
        with network_disabled():
            return await self._load_offline()

    async def _load_offline(self) -> ReplayInputs:
        """Normalize one recorded scenario through production pure helpers."""
        routes = self._route_candidates(self.scenario.read_json_fixture("route_candidates"))
        clock = self.scenario.clock
        parsed_alerts, parsed_vehicles = self._parse_gtfs_replay_fixtures()
        bus_payload = self.scenario.read_json_fixture("bus_vehicle_positions")
        if not isinstance(bus_payload, dict):
            _invalid("bus_vehicle_positions must be a SIRI provider object")
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
        self._require_stalled_vehicle_evidence(stalled_trains, bus_positions)
        grok_rows = self._list_of_objects(self.scenario.read_json_fixture("grok_x"), "grok_x")
        grok_rows.extend(self._list_of_objects(self.scenario.read_json_fixture("grok_web"), "grok_web"))
        # Keep each provider-shaped row separate until the comparison runner
        # applies its source ablation. Merging here would retain a disabled
        # source inside an already-merged record and invalidate the replay.
        grok_incidents = [dict(row) for row in grok_rows]
        snapshot, incidents = await self._load_ny511_replay(clock)
        matches = match_cached_incidents(incidents, extract_candidate_stop_context(routes))
        ticketmaster_events = self._load_ticketmaster_events()
        advisor_outputs, advisor_ablation_outputs = self._advisor_outputs(len(routes))
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
            advisor_ablation_outputs=advisor_ablation_outputs,
        )
