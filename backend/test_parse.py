"""
Test parse_response by fetching a live route and parsing it.
Usage: python test_parse.py
"""
import asyncio
import json
from dotenv import load_dotenv

ORIGIN = (40.6501, -73.9796)
DEST = "Times Square, New York, NY"


async def main():
    load_dotenv()
    from app.services.directions import get_transit_route, parse_response

    print(f"Origin: {ORIGIN}")
    print(f"Dest:   {DEST}\n")

    raw = await get_transit_route(ORIGIN, DEST)

    if "error" in raw:
        print("API error:", raw["error"]["message"])
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
                print(f"  Step {j + 1}: {step['type']} - {step['train_line']} toward {step['direction']}")
                print(f"    {step['departure_stop']} -> {step['arrival_stop']} ({step['stop_count']} stops)")
                print(f"    train in {step['minutes_until_train_arrives']:.1f} min, arrive in {step['minutes_until_arrival']:.1f} min")
                print(f"    color: {step['line_color']}")
        print()

    print("=== Full parsed JSON ===")
    print(json.dumps(parsed, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
