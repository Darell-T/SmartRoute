"""stops-for-route parsing and board/exit slicing.

Pure-function tests for app.services.bus_routes -- no network. Covers both
OneBusAway payload flavors, group selection by travel direction, the snap
distance cap, and Google->OBA route id normalization.
"""

from app.services.bus_routes import (
    normalize_google_bus_route_id,
    parse_stops_for_route,
    slice_route_stops,
)


def _stop(stop_id, name, lat, lon):
    return {"id": stop_id, "name": name, "lat": lat, "lon": lon}


# Northbound group runs south->north along a fake avenue; southbound reversed.
NORTH_STOPS = [
    _stop("MTA_100", "AV A/1 ST", 40.7000, -73.9900),
    _stop("MTA_101", "AV A/3 ST", 40.7030, -73.9905),
    _stop("MTA_102", "AV A/5 ST", 40.7060, -73.9910),
    _stop("MTA_103", "AV A/7 ST", 40.7090, -73.9915),
]
SOUTH_STOPS = [
    _stop("MTA_200", "AV B/7 ST", 40.7090, -73.9885),
    _stop("MTA_201", "AV B/5 ST", 40.7060, -73.9880),
    _stop("MTA_202", "AV B/3 ST", 40.7030, -73.9875),
    _stop("MTA_203", "AV B/1 ST", 40.7000, -73.9870),
]

VANILLA_PAYLOAD = {
    "data": {
        "entry": {
            "stopGroupings": [
                {
                    "stopGroups": [
                        {"id": "0", "stopIds": [s["id"] for s in NORTH_STOPS]},
                        {"id": "1", "stopIds": [s["id"] for s in SOUTH_STOPS]},
                    ],
                },
            ],
        },
        "references": {"stops": NORTH_STOPS + SOUTH_STOPS},
    },
}

MTA_FLAT_PAYLOAD = {
    "data": {
        "stops": NORTH_STOPS + SOUTH_STOPS,
        "stopGroupings": [
            {
                "stopGroups": [
                    {"id": "0", "stopIds": [s["id"] for s in NORTH_STOPS]},
                    {"id": "1", "stopIds": [s["id"] for s in SOUTH_STOPS]},
                ],
            },
        ],
    },
}


def test_parses_vanilla_oba_payload():
    parsed = parse_stops_for_route(VANILLA_PAYLOAD)
    assert len(parsed["stops_by_id"]) == 8
    assert parsed["stops_by_id"]["MTA_101"]["name"] == "AV A/3 ST"
    assert parsed["ordered_groups"] == [
        [s["id"] for s in NORTH_STOPS],
        [s["id"] for s in SOUTH_STOPS],
    ]


def test_parses_mta_flat_payload():
    parsed = parse_stops_for_route(MTA_FLAT_PAYLOAD)
    assert len(parsed["stops_by_id"]) == 8
    assert len(parsed["ordered_groups"]) == 2


def test_malformed_payloads_yield_empty():
    for payload in ({}, {"data": None}, {"data": {"stops": "nope"}}):
        parsed = parse_stops_for_route(payload)
        assert parsed["stops_by_id"] == {}
        assert parsed["ordered_groups"] == []


def test_slices_between_board_and_exit_in_travel_direction():
    parsed = parse_stops_for_route(VANILLA_PAYLOAD)
    # Northbound trip: board near AV A/1 ST, exit near AV A/7 ST.
    sliced = slice_route_stops(
        parsed,
        board_coords={"latitude": 40.7001, "longitude": -73.9899},
        exit_coords={"latitude": 40.7091, "longitude": -73.9914},
    )
    assert [s["name"] for s in sliced] == [
        "AV A/1 ST",
        "AV A/3 ST",
        "AV A/5 ST",
        "AV A/7 ST",
    ]
    assert sliced[0] == {"name": "AV A/1 ST", "lat": 40.7000, "lng": -73.9900}


def test_rejects_group_where_exit_precedes_board():
    parsed = parse_stops_for_route(VANILLA_PAYLOAD)
    # Southbound trip: board at 7 ST, exit at 1 ST -- must pick the SOUTH
    # group (AV B), not the north group reversed.
    sliced = slice_route_stops(
        parsed,
        board_coords={"latitude": 40.7090, "longitude": -73.9886},
        exit_coords={"latitude": 40.7000, "longitude": -73.9871},
    )
    assert [s["name"] for s in sliced] == [
        "AV B/7 ST",
        "AV B/5 ST",
        "AV B/3 ST",
        "AV B/1 ST",
    ]


def test_snap_cap_rejects_distant_coords():
    parsed = parse_stops_for_route(VANILLA_PAYLOAD)
    sliced = slice_route_stops(
        parsed,
        board_coords={"latitude": 40.7500, "longitude": -73.9900},  # ~5.5km away
        exit_coords={"latitude": 40.7091, "longitude": -73.9914},
        max_snap_m=250,
    )
    assert sliced == []


def test_adjacent_board_exit_yields_two_stops():
    parsed = parse_stops_for_route(VANILLA_PAYLOAD)
    sliced = slice_route_stops(
        parsed,
        board_coords={"latitude": 40.7000, "longitude": -73.9900},
        exit_coords={"latitude": 40.7030, "longitude": -73.9905},
    )
    assert [s["name"] for s in sliced] == ["AV A/1 ST", "AV A/3 ST"]


def test_normalize_google_bus_route_id():
    assert normalize_google_bus_route_id("M15-SBS") == ["M15-SBS", "M15+"]
    assert normalize_google_bus_route_id(" b41 ") == ["B41"]
    assert normalize_google_bus_route_id("Q44-SBS") == ["Q44-SBS", "Q44+"]
