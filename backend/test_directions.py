"""
Quick test script to call Google Routes API and print the JSON response.
Usage: python test_directions.py

If the API isn't enabled yet, prints a sample response so you can see the structure.
"""
import asyncio
import json
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


async def main():
    load_dotenv()
    from app.services.directions import get_transit_route

    print(f"Origin: {ORIGIN}")
    print(f"Dest:   {DEST}\n")

    result = await get_transit_route(ORIGIN, DEST)

    if "error" in result:
        print("API returned an error:")
        print(json.dumps(result, indent=2))
        print("\n" + "=" * 60)
        print("SAMPLE RESPONSE (what you'll get when the API is enabled):")
        print("=" * 60 + "\n")
        print(json.dumps(SAMPLE_RESPONSE, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
