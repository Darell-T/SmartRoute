"""Focused tests for the coarse incident batch taxonomy."""

from __future__ import annotations

import dataclasses

import pytest
from app.services.incidents.batches import (
    INCIDENT_BATCHES,
    NYC_ENVELOPE,
    IncidentBatch,
    coverage_batch_ids_for_point,
    get_incident_batch,
    incident_batch_ids,
)

EXPECTED_IDS: tuple[str, ...] = (
    "upper-manhattan",
    "midtown-manhattan",
    "lower-manhattan",
    "downtown-northwest-brooklyn",
    "central-south-brooklyn",
    "western-queens",
    "eastern-queens-jamaica",
    "south-bronx",
    "central-north-bronx",
    "staten-island-interborough-bus",
)


def test_exact_ids_and_order() -> None:
    assert incident_batch_ids() == EXPECTED_IDS
    assert tuple(batch.batch_id for batch in INCIDENT_BATCHES) == EXPECTED_IDS


def test_exactly_ten_unique_immutable_batches() -> None:
    assert len(INCIDENT_BATCHES) == 10
    ids = [batch.batch_id for batch in INCIDENT_BATCHES]
    assert len(set(ids)) == 10
    assert isinstance(INCIDENT_BATCHES, tuple)
    assert all(isinstance(batch, IncidentBatch) for batch in INCIDENT_BATCHES)
    for batch in INCIDENT_BATCHES:
        with pytest.raises(dataclasses.FrozenInstanceError):
            batch.batch_id = "mutated"
        # Slots forbid dynamic fields: the instance has no __dict__, and any
        # attempt to add one fails (AttributeError on older interpreters,
        # TypeError from the 3.12 generated __setattr__ fallback).
        assert not hasattr(batch, "__dict__")
        with pytest.raises((AttributeError, TypeError)):
            batch.unexpected_field = "nope"


def test_bounds_valid_ordered_nonzero_inside_envelope() -> None:
    env_south, env_west, env_north, env_east = NYC_ENVELOPE
    for batch in INCIDENT_BATCHES:
        south, west, north, east = batch.bounds
        assert isinstance(south, float)
        assert isinstance(west, float)
        assert isinstance(north, float)
        assert isinstance(east, float)
        assert south < north, batch.batch_id
        assert west < east, batch.batch_id
        assert north - south > 0.0
        assert east - west > 0.0
        assert env_south <= south < north <= env_north, batch.batch_id
        assert env_west <= west < east <= env_east, batch.batch_id


def test_bounded_boroughs_and_focus_terms_no_empty_strings() -> None:
    for batch in INCIDENT_BATCHES:
        assert batch.batch_id.strip()
        assert batch.label.strip()
        assert 1 <= len(batch.boroughs) <= 2, batch.batch_id
        assert 2 <= len(batch.focus_terms) <= 6, batch.batch_id
        assert all(isinstance(value, str) and value.strip() for value in batch.boroughs)
        assert all(isinstance(value, str) and value.strip() for value in batch.focus_terms)
        assert all(len(value) <= 40 for value in batch.focus_terms)


def test_lookup_normalization_and_unknown_ids() -> None:
    for batch in INCIDENT_BATCHES:
        assert get_incident_batch(batch.batch_id) is batch
        assert get_incident_batch(f"  {batch.batch_id.upper()}  ") is batch
        assert get_incident_batch(batch.batch_id.casefold()) is batch
    assert get_incident_batch("upper-manhatten") is None  # typo: no fuzzy matching
    assert get_incident_batch("manhattan") is None
    assert get_incident_batch("") is None
    assert get_incident_batch("   ") is None
    assert get_incident_batch("BRONX") is None


def test_no_per_station_or_station_sized_fanout_surface() -> None:
    field_names = [field.name for field in dataclasses.fields(IncidentBatch)]
    assert field_names == ["batch_id", "label", "boroughs", "bounds", "focus_terms"]
    assert not any("station" in name or "stop" in name for name in field_names)
    # No station-sized fanout: every batch carries the same small fixed fields.
    assert len(field_names) == 5
    for batch in INCIDENT_BATCHES:
        assert len(batch.focus_terms) <= 6
        assert len(batch.boroughs) <= 2
        assert batch.bounds == tuple(batch.bounds)
        assert len(batch.bounds) == 4


def test_point_matches_one_canonical_batch() -> None:
    # Times Square sits inside midtown-manhattan only.
    assert coverage_batch_ids_for_point(40.7580, -73.9850) == ("midtown-manhattan",)
    # Church Av (Brooklyn) sits inside central-south-brooklyn only.
    assert coverage_batch_ids_for_point(40.650, -73.963) == ("central-south-brooklyn",)


def test_point_matches_overlapping_batches_in_canonical_order() -> None:
    # (40.72, -73.96) is inside lower-manhattan, downtown-northwest-brooklyn,
    # and western-queens; results stay in canonical batch order.
    assert coverage_batch_ids_for_point(40.72, -73.96) == (
        "lower-manhattan",
        "downtown-northwest-brooklyn",
        "western-queens",
    )


def test_inclusive_bounds_match_shared_corners_and_edges() -> None:
    # A shared corner belongs to every batch whose inclusive bounds contain it.
    assert coverage_batch_ids_for_point(40.79, -73.96) == (
        "upper-manhattan",
        "midtown-manhattan",
        "western-queens",
    )
    assert coverage_batch_ids_for_point(40.73, -74.02) == (
        "midtown-manhattan",
        "lower-manhattan",
    )


def test_invalid_and_outside_points_match_no_batch() -> None:
    assert coverage_batch_ids_for_point(0, 0) == ()
    assert coverage_batch_ids_for_point(91, -73.9) == ()
    assert coverage_batch_ids_for_point(-90, 180) == ()
    assert coverage_batch_ids_for_point(41.0, -73.9) == ()  # north of the envelope
    assert coverage_batch_ids_for_point(40.75, -73.5) == ()  # east of the envelope
    # Inside the NYC envelope but between all coarse batches.
    assert coverage_batch_ids_for_point(40.95, -73.65) == ()
    for bad in (
        (None, None),
        ("40.75", None),
        (float("inf"), -73.9),
        ("north", "west"),
    ):
        assert coverage_batch_ids_for_point(*bad) == ()
