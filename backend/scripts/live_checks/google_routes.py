"""Manual Google Routes probes with an explicit live-request gate.

Usage: ``python -m scripts.live_checks.google_routes --live`` for the raw
provider response, or add ``--parsed`` to display normalized route steps.
Without ``--live`` the default raw mode prints the sample response and never
imports or calls the provider.
"""
import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

# Church Ave, Brooklyn → Times Square
ORIGIN = (40.6501, -73.9796)
DEST = "Times Square, New York, NY"

# Based on the FIELD_MASK in directions.py, this is the response structure
# you'll get back from Google Routes API for a subway transit query.
SAMPLE_RESPONSE = {
    "routes": [
        {
            "legs": [
                {
                    "distanceMeters": 14200,
                    "duration": "2340s",
                    "polyline": {
                        "encodedPolyline": "wzwwFt`ubMgBxA..."
                    },
                    "steps": [
                        {
                            "travelMode": "WALK",
                            "startLocation": {
                                "latLng": {
                                    "latitude": 40.6501,
                                    "longitude": -73.9796
                                }
                            },
                            "endLocation": {
                                "latLng": {
                                    "latitude": 40.6505,
                                    "longitude": -73.9793
                                }
                            },
                            "staticDuration": "120s",
                            "polyline": {
                                "encodedPolyline": "abc123..."
                            }
                        },
                        {
                            "travelMode": "TRANSIT",
                            "startLocation": {
                                "latLng": {
                                    "latitude": 40.6505,
                                    "longitude": -73.9793
                                }
                            },
                            "endLocation": {
                                "latLng": {
                                    "latitude": 40.7580,
                                    "longitude": -73.9855
                                }
                            },
                            "staticDuration": "1800s",
                            "polyline": {
                                "encodedPolyline": "def456..."
                            },
                            "transitDetails": {
                                "stopDetails": {
                                    "arrivalStop": {
                                        "name": "Times Sq-42 St",
                                        "location": {
                                            "latLng": {
                                                "latitude": 40.7580,
                                                "longitude": -73.9855
                                            }
                                        }
                                    },
                                    "departureStop": {
                                        "name": "Church Av",
                                        "location": {
                                            "latLng": {
                                                "latitude": 40.6505,
                                                "longitude": -73.9793
                                            }
                                        }
                                    },
                                    "arrivalTime": "2026-03-22T18:30:00Z",
                                    "departureTime": "2026-03-22T18:00:00Z"
                                },
                                "headsign": "Astoria-Ditmars Blvd",
                                "transitLine": {
                                    "name": "Q Church Av Local / Brighton Express",
                                    "nameShort": "Q",
                                    "color": "#FCCC0A",
                                    "textColor": "#000000",
                                    "agencies": [
                                        {
                                            "name": "MTA New York City Transit",
                                            "uri": "https://new.mta.info/"
                                        }
                                    ],
                                    "vehicle": {
                                        "name": {
                                            "text": "Subway"
                                        },
                                        "type": "SUBWAY",
                                        "iconUri": "//maps.gstatic.com/mapfiles/transit/iw2/6/subway2.png"
                                    }
                                },
                                "stopCount": 15
                            }
                        },
                        {
                            "travelMode": "WALK",
                            "startLocation": {
                                "latLng": {
                                    "latitude": 40.7580,
                                    "longitude": -73.9855
                                }
                            },
                            "endLocation": {
                                "latLng": {
                                    "latitude": 40.7580,
                                    "longitude": -73.9855
                                }
                            },
                            "staticDuration": "60s",
                            "polyline": {
                                "encodedPolyline": "ghi789..."
                            }
                        }
                    ]
                }
            ]
        }
    ]
}


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a manual Google Routes probe.")
    parser.add_argument("--live", action="store_true", help="Allow one Google Routes request.")
    parser.add_argument("--parsed", action="store_true", help="Display parsed route steps instead of raw JSON.")
    return parser.parse_args(argv)


def _print_sample() -> None:
    print("SAMPLE RESPONSE (what you'll get when the API is enabled):")
    print("=" * 60)
    print(json.dumps(SAMPLE_RESPONSE, indent=2))


async def _raw_probe() -> None:
    from app.services.directions import get_transit_route

    print(f"Origin: {ORIGIN}")
    print(f"Dest:   {DEST}\n")

    result = await get_transit_route(ORIGIN, DEST)

    if "error" in result:
        print("API returned an error:")
        print(json.dumps(result, indent=2))
        print()
        _print_sample()
    else:
        print(json.dumps(result, indent=2))


async def _parsed_probe() -> None:
    from app.services.directions import get_transit_route, parse_response

    print(f"Origin: {ORIGIN}")
    print(f"Dest:   {DEST}\n")

    raw = await get_transit_route(ORIGIN, DEST)

    if "error" in raw:
        print("API error:", raw["error"].get("message", "unknown error"))
        return

    parsed = parse_response(raw)

    print(f"Found {len(parsed)} route(s)\n")
    for i, steps in enumerate(parsed):
        print(f"=== Route {i + 1} ({len(steps)} steps) ===")
        for j, step in enumerate(steps):
            if step["type"] == "WALK":
                print(f"  Step {j + 1}: WALK")
                print(f"    from: {step['start_point']}")
                print(f"    to:   {step['end_point']}")
            else:
                print(
                    f"  Step {j + 1}: {step['type']} - "
                    f"{step['train_line']} toward {step['direction']}"
                )
                print(
                    f"    {step['departure_stop']} -> {step['arrival_stop']} "
                    f"({step['stop_count']} stops)"
                )
                print(
                    f"    train in {step['minutes_until_train_arrives']:.1f} min, "
                    f"arrive in {step['minutes_until_arrival']:.1f} min"
                )
                print(f"    color: {step['line_color']}")
        print()

    print("=== Full parsed JSON ===")
    print(json.dumps(parsed, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    if not args.live:
        print("SKIPPED: pass --live to allow a Google Routes request")
        if not args.parsed:
            _print_sample()
        return 0

    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
    asyncio.run(_parsed_probe() if args.parsed else _raw_probe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
