"""Route scoring + route-step accessors.

Pure functions over Google-parsed route step dicts. Depends only on ``text``
(for ``_safe_text``). ``_step_route_id`` lives here (its primary consumer is
``_route_lines``); ``incidents`` imports it from here rather than duplicating it.
"""

from app.services.trips import text


def _step_minutes(step: dict) -> int:
    if step.get("type") in ("SUBWAY", "BUS"):
        minutes = step.get("minutes_until_arrival")
        if isinstance(minutes, (int, float)):
            return max(1, round(minutes))
        return 8
    return 4

def _route_total_minutes(route: list[dict]) -> int:
    for step in route or []:
        route_total = step.get("route_total_minutes")
        if isinstance(route_total, (int, float)):
            return max(1, round(route_total))
    live_arrivals = [
        step.get("minutes_until_arrival")
        for step in route or []
        if step.get("type") in ("SUBWAY", "BUS")
        and isinstance(step.get("minutes_until_arrival"), (int, float))
    ]
    if live_arrivals:
        return max(1, round(max(live_arrivals)))
    return max(1, sum(_step_minutes(step) for step in route))

def _route_transfer_count(route: list[dict]) -> int:
    transit_steps = [step for step in route if step.get("type") in ("SUBWAY", "BUS")]
    return max(0, len(transit_steps) - 1)

def _step_route_id(step: dict) -> str:
    return str(step.get("route_id") or step.get("train_line") or "").strip().upper()

def _route_lines(route: list[dict]) -> list[str]:
    lines: list[str] = []
    for step in route or []:
        if step.get("type") not in ("SUBWAY", "BUS"):
            continue
        line = _step_route_id(step)
        if line and line not in lines:
            lines.append(line)
    return lines

def _route_alert_hits(route: list[dict], alerts: list[dict] | None) -> list[str]:
    route_lines = set(_route_lines(route))
    hits: list[str] = []
    for alert in alerts or []:
        alert_routes = {
            str(route_id or "").strip().upper()
            for route_id in alert.get("route_ids", [])
            if str(route_id or "").strip()
        }
        if route_lines & alert_routes:
            title = text._safe_text(alert.get("header") or "active alert", 80)
            if title and title not in hits:
                hits.append(title)
    return hits

def _route_score(route: list[dict], alerts: list[dict] | None) -> dict:
    total_minutes = _route_total_minutes(route)
    transfers = _route_transfer_count(route)
    alert_hits = _route_alert_hits(route, alerts)
    transit_count = len(_route_lines(route))
    score = total_minutes + transfers * 4 + len(alert_hits) * 8
    return {
        "total_minutes": total_minutes,
        "transfers": transfers,
        "alert_count": len(alert_hits),
        "transit_count": transit_count,
        "score": score,
        "alerts": alert_hits[:2],
    }

def _score_routes(routes: list[list[dict]], alerts: list[dict] | None) -> list[dict]:
    scored = []
    for index, route in enumerate(routes):
        score = _route_score(route, alerts)
        scored.append({"index": index, **score})
    scored.sort(
        key=lambda row: (
            row["score"],
            row["total_minutes"],
            row["transfers"],
            row["index"],
        )
    )
    rank_by_index = {row["index"]: rank + 1 for rank, row in enumerate(scored)}
    for row in scored:
        row["rank"] = rank_by_index[row["index"]]
    return scored

def _score_by_index(scored_routes: list[dict]) -> dict[int, dict]:
    return {int(row["index"]): row for row in scored_routes}
