"""stops-for-location payload parsing.

MTA BusTime's OneBusAway flavor returns the stop array under
``data.stops`` (with ``limitExceeded`` alongside), while vanilla
OneBusAway uses ``data.list``. Reading only ``data.list`` silently
yielded zero nearby bus stops everywhere, which killed the entire
bus-arrivals pipeline.
"""

from app.services.mta.bus import _bus_stop_record, _stops_for_location_list

MTA_SHAPE = {
    "code": 200,
    "data": {
        "limitExceeded": False,
        "stops": [
            {"id": "MTA_404923", "lat": 40.7519, "lon": -73.9786, "name": "MADISON AV/E 43 ST"},
            {"id": "MTA_400069", "lat": 40.7484, "lon": -73.9881, "name": "BROADWAY/W 34 ST"},
        ],
    },
}

VANILLA_OBA_SHAPE = {
    "code": 200,
    "data": {
        "list": [
            {"id": "S_1", "lat": 40.7, "lon": -74.0, "name": "Stop"},
        ],
    },
}


def test_mta_bustime_data_stops_shape_is_parsed():
    stops = _stops_for_location_list(MTA_SHAPE)
    assert len(stops) == 2
    assert stops[0]["name"] == "MADISON AV/E 43 ST"


def test_vanilla_oba_data_list_shape_still_works():
    stops = _stops_for_location_list(VANILLA_OBA_SHAPE)
    assert len(stops) == 1


def test_empty_or_malformed_payloads_yield_empty_list():
    assert _stops_for_location_list({}) == []
    assert _stops_for_location_list({"data": None}) == []
    assert _stops_for_location_list({"data": {"stops": "nope"}}) == []


def test_bus_stop_record_keeps_oba_compass_direction():
    stop = {
        "id": "MTA_404923",
        "name": "MADISON AV/E 43 ST",
        "lat": 40.7519,
        "lon": -73.9786,
        "direction": "NE",
        "routes": [],
    }
    record = _bus_stop_record(stop, distance_m=150.0)
    assert record["stop_compass"] == "NE"
    assert record["stop_id"] == "MTA_404923"
    assert record["distance_m"] == 150.0


def test_bus_stop_record_tolerates_missing_direction():
    stop = {"id": "S_1", "name": "Stop", "lat": 40.7, "lon": -74.0}
    record = _bus_stop_record(stop, distance_m=10.0)
    assert record["stop_compass"] == ""
