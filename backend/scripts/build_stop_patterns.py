"""Precompute static GTFS stop patterns into an in-memory-loadable artifact.

Fix B: trip enrichment must NOT query the remote Postgres at runtime. This
offline build groups GTFS trips into distinct stop patterns
(route_id, direction_id, ordered parent stop ids) and emits a small JSON the
backend loads once at startup.

Transfer components: explicit cross-stop transfers.txt relationships are
projected into the same artifact as deterministic ``gtfs_transfer:<id>``
component identities on the member parent stops. This is the no-request-DB
deployment projection of canonical GTFS; it is NOT the numeric MTA Subway
Stations CSV Complex ID.

Sources (pick one):
  --sqlite PATH    read stops/routes/trips/stop_times from a local SQLite GTFS db
                   (the dev path; direction_id is overlaid from --trips-txt;
                   transfers come from the local transfers table).
  --zip PATH       read stops.txt/routes.txt/trips.txt/stop_times.txt from a
                   GTFS .zip (the canonical/reproducible path; has direction_id
                   and transfers.txt).
  (default)        download the MTA supplemented GTFS zip and build from it.

Output: backend/app/data/stop_patterns.json
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sqlite3
import tempfile
import zipfile
from collections import Counter, defaultdict
from contextlib import suppress
from pathlib import Path

SUPPLEMENTED_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_supplemented.zip"
_BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUT = _BACKEND_DIR / "app" / "data" / "stop_patterns.json"
DEFAULT_SQLITE = _BACKEND_DIR / "data" / "gtfs_current.db"
DEFAULT_TRIPS_TXT = _BACKEND_DIR / "data" / "gtfs_static" / "trips.txt"


def strip_dir(stop_id: str) -> str:
    """Platform stop id -> parent station id, matching the runtime convention
    (e.g. 'Q05N' -> 'Q05'). Mirrors stop_id.rstrip('NS') used elsewhere."""
    return (stop_id or "").rstrip("NS")


def _pattern_signature(route_id: str, direction, stop_ids: list[str]) -> str:
    raw = f"{route_id}|{direction if direction is not None else ''}|{','.join(stop_ids)}"
    # ponytail: SHA-1 is a stable artifact identifier, not a security digest.
    # Keep the existing 16-hex prefix so stop_patterns.json identities stay
    # unchanged; switch to SHA-256 if those identities are ever versioned.
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:16]


def _transfer_adjacency(
    stops_by_id: dict[str, dict],
    transfer_rows: list[tuple[str, str]],
) -> dict[str, set[str]]:
    edges: set[tuple[str, str]] = set()
    adjacency: dict[str, set[str]] = defaultdict(set)
    for raw_from, raw_to in transfer_rows:
        from_id = strip_dir(raw_from or "")
        to_id = strip_dir(raw_to or "")
        if not from_id or not to_id or from_id == to_id:
            continue
        if from_id not in stops_by_id or to_id not in stops_by_id:
            continue
        edge = tuple(sorted((from_id, to_id)))
        if edge in edges:
            continue
        edges.add(edge)
        adjacency[from_id].add(to_id)
        adjacency[to_id].add(from_id)
    return adjacency


def _component_members(start: str, adjacency: dict[str, set[str]], visited: set[str]) -> list[str]:
    members: list[str] = []
    stack = [start]
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        members.append(node)
        stack.extend(adjacency[node] - visited)
    return members


def derive_transfer_components(
    stops_by_id: dict[str, dict],
    transfer_rows: list[tuple[str, str]],
) -> dict[str, str]:
    """Parent stop id -> deterministic GTFS transfer-component identity.

    Connected components are derived ONLY from explicit cross-stop transfer
    relationships whose endpoints are known parent stops. Self rows and rows
    with unknown endpoints are ignored; nothing is inferred from station
    names, coordinates, route overlap, or min_transfer_time. Each multi-parent
    component gets an opaque, source-qualified identity
    ``gtfs_transfer:<lowest member id>`` (sorted members, so deterministic).
    Singleton stops get no identity -- never a fabricated complex.
    """
    adjacency = _transfer_adjacency(stops_by_id, transfer_rows)
    components: dict[str, str] = {}
    visited: set[str] = set()
    for start in sorted(stops_by_id):
        if start in visited or start not in adjacency:
            continue
        members = _component_members(start, adjacency, visited)
        if len(members) > 1:
            identity = f"gtfs_transfer:{min(members)}"
            for member in members:
                components[member] = identity
    return components


def _parent_sequence(seq: list[str]) -> list[str]:
    ordered: list[str] = []
    for sid in seq:
        pid = strip_dir(sid)
        if pid and (not ordered or ordered[-1] != pid):
            ordered.append(pid)
    return ordered


def _aggregate_patterns(
    trip_meta: dict[str, tuple],
    trip_sequences: dict[str, list[str]],
) -> dict[tuple, dict]:
    agg: dict[tuple, dict] = {}
    for trip_id, seq in trip_sequences.items():
        meta = trip_meta.get(trip_id)
        if not meta:
            continue
        route_id, direction = meta
        ordered = _parent_sequence(seq)
        if len(ordered) < 2:
            continue
        key = (route_id, tuple(ordered))
        entry = agg.get(key)
        if entry is None:
            entry = agg[key] = {
                "route_id": route_id,
                "stop_ids": ordered,
                "trip_count": 0,
                "directions": Counter(),
            }
        entry["trip_count"] += 1
        if direction is not None:
            entry["directions"][direction] += 1
    return agg


def _pattern_rows(
    agg: dict[tuple, dict],
    routes_short: dict[str, str],
    min_trip_count: int,
) -> tuple[list[dict], set[str]]:
    patterns = []
    used_stop_ids: set[str] = set()
    for (route_id, _ids), entry in agg.items():
        if entry["trip_count"] < min_trip_count:
            continue
        stop_ids = entry["stop_ids"]
        dirs = entry["directions"]
        direction = dirs.most_common(1)[0][0] if dirs else None
        patterns.append({
            "route_id": route_id,
            "route_short_name": routes_short.get(route_id, route_id),
            "direction_id": direction,
            "trip_count": entry["trip_count"],
            "signature": _pattern_signature(route_id, direction, stop_ids),
            "stop_ids": stop_ids,
        })
        used_stop_ids.update(stop_ids)
    patterns.sort(key=lambda p: (-p["trip_count"], p["route_id"]))
    return patterns, used_stop_ids


def _artifact_stops(
    used_stop_ids: set[str],
    stops_by_id: dict[str, dict],
    transfer_components: dict[str, str] | None,
) -> dict[str, dict]:
    stops = {}
    for sid in sorted(used_stop_ids):
        if sid not in stops_by_id:
            continue
        if transfer_components and sid in transfer_components:
            stops[sid] = {**stops_by_id[sid], "station_complex_id": transfer_components[sid]}
        else:
            stops[sid] = stops_by_id[sid]
    return stops


def build_patterns(
    stops_by_id: dict[str, dict],
    routes_short: dict[str, str],
    trip_meta: dict[str, tuple],
    trip_sequences: dict[str, list[str]],
    min_trip_count: int = 1,
    transfer_components: dict[str, str] | None = None,
) -> dict:
    """Pure core: group trips into distinct stop patterns. Inputs:
      stops_by_id     {parent_stop_id: {"name","lat","lon"}}
      routes_short    {route_id: route_short_name}
      trip_meta       {trip_id: (route_id, direction_id|None)}
      trip_sequences  {trip_id: [platform_stop_id, ... ordered by stop_sequence]}
      transfer_components  optional {parent_stop_id: gtfs_transfer:<id>} map
                           from derive_transfer_components()
    Returns the artifact dict. Patterns below min_trip_count are dropped."""
    # Group by (route_id, ordered parent stop ids). The ordered ids already
    # encode direction (northbound is the reverse of southbound), so direction_id
    # is kept only as metadata -- the dominant value among the grouped trips.
    # This avoids splitting a pattern when the direction overlay is partial.
    patterns, used_stop_ids = _pattern_rows(
        _aggregate_patterns(trip_meta, trip_sequences), routes_short, min_trip_count
    )
    stops = _artifact_stops(used_stop_ids, stops_by_id, transfer_components)
    artifact = {
        "source": "gtfs_supplemented",
        "route_count": len({p["route_id"] for p in patterns}),
        "pattern_count": len(patterns),
        "stop_count": len(stops),
        "stops": stops,
        "patterns": patterns,
    }
    if transfer_components:
        artifact["transfer_components"] = {
            "count": len(set(transfer_components.values())),
            "member_stop_count": len(transfer_components),
            "identity_prefix": "gtfs_transfer",
            "source": "transfers",
        }
    return artifact


# --------------------------------------------------------------------------
# Source adapters
# --------------------------------------------------------------------------
def _parse_trip_directions(trips_txt: Path | None) -> dict[str, int]:
    """trip_id -> direction_id from a loose trips.txt (best-effort overlay)."""
    out: dict[str, int] = {}
    if not trips_txt or not trips_txt.exists():
        return out
    with open(trips_txt, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = row.get("trip_id")
            d = row.get("direction_id")
            if tid and d not in (None, ""):
                with suppress(ValueError):
                    out[tid] = int(d)
    return out


def load_from_sqlite(sqlite_path: Path, trips_txt: Path | None):
    con = sqlite3.connect(str(sqlite_path))
    cur = con.cursor()

    stops_by_id: dict[str, dict] = {}
    for sid, name, lat, lon in cur.execute(
        "SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops"
    ):
        pid = strip_dir(sid)
        if not pid or pid in stops_by_id:
            continue
        try:
            stops_by_id[pid] = {"name": name, "lat": float(lat), "lon": float(lon)}
        except (TypeError, ValueError):
            continue

    routes_short = {
        rid: (short or rid)
        for rid, short in cur.execute("SELECT route_id, route_short_name FROM routes")
    }

    directions = _parse_trip_directions(trips_txt)
    trip_meta: dict[str, tuple] = {}
    for trip_id, route_id in cur.execute("SELECT trip_id, route_id FROM trips"):
        trip_meta[trip_id] = (route_id, directions.get(trip_id))

    trip_sequences: dict[str, list[str]] = defaultdict(list)
    rows = cur.execute(
        "SELECT trip_id, stop_id, stop_sequence FROM stop_times "
        "ORDER BY trip_id, stop_sequence"
    )
    for trip_id, stop_id, _seq in rows:
        trip_sequences[trip_id].append(stop_id)

    transfer_rows = [
        (from_id, to_id)
        for from_id, to_id in cur.execute(
            "SELECT from_stop_id, to_stop_id FROM transfers"
        )
    ]
    con.close()
    return stops_by_id, routes_short, trip_meta, dict(trip_sequences), transfer_rows


def _zip_rows(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as handle:
        yield from csv.DictReader(io.TextIOWrapper(handle, "utf-8"))


def _zip_stops(zf: zipfile.ZipFile) -> dict[str, dict]:
    stops_by_id: dict[str, dict] = {}
    for row in _zip_rows(zf, "stops.txt"):
        pid = strip_dir(row.get("stop_id", ""))
        if not pid or pid in stops_by_id:
            continue
        try:
            stops_by_id[pid] = {
                "name": row.get("stop_name"),
                "lat": float(row["stop_lat"]),
                "lon": float(row["stop_lon"]),
            }
        except (TypeError, ValueError, KeyError):
            continue
    return stops_by_id


def _zip_routes(zf: zipfile.ZipFile) -> dict[str, str]:
    routes_short: dict[str, str] = {}
    try:
        for row in _zip_rows(zf, "routes.txt"):
            rid = row.get("route_id")
            if rid:
                routes_short[rid] = row.get("route_short_name") or rid
    except KeyError:
        return routes_short
    return routes_short


def _zip_trips(zf: zipfile.ZipFile) -> dict[str, tuple]:
    trip_meta: dict[str, tuple] = {}
    for row in _zip_rows(zf, "trips.txt"):
        tid = row.get("trip_id")
        if not tid:
            continue
        d = row.get("direction_id")
        direction = int(d) if d not in (None, "") and d.isdigit() else None
        trip_meta[tid] = (row.get("route_id"), direction)
    return trip_meta


def _zip_stop_times(zf: zipfile.ZipFile) -> dict[str, list[str]]:
    trip_sequences: dict[str, list[tuple]] = defaultdict(list)
    for row in _zip_rows(zf, "stop_times.txt"):
        tid = row.get("trip_id")
        if not tid:
            continue
        try:
            seq = int(row["stop_sequence"])
        except (TypeError, ValueError, KeyError):
            continue
        trip_sequences[tid].append((seq, row.get("stop_id", "")))
    return {tid: [sid for _s, sid in sorted(pairs)] for tid, pairs in trip_sequences.items()}


def load_from_zip(zip_path: Path):
    with zipfile.ZipFile(zip_path) as zf:
        stops_by_id = _zip_stops(zf)
        routes_short = _zip_routes(zf)
        trip_meta = _zip_trips(zf)
        ordered = _zip_stop_times(zf)
        # transfers.txt is canonical input: a zip without it must fail loudly
        # instead of silently building an artifact with no complex metadata.
        transfer_rows = [
            (row.get("from_stop_id", ""), row.get("to_stop_id", ""))
            for row in _zip_rows(zf, "transfers.txt")
        ]
    return stops_by_id, routes_short, trip_meta, ordered, transfer_rows


def _download_zip() -> Path:
    import httpx

    print(f"Downloading GTFS zip from {SUPPLEMENTED_URL} ...")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as f:
        with httpx.stream("GET", SUPPLEMENTED_URL, timeout=120, follow_redirects=True) as resp:
            for chunk in resp.iter_bytes():
                f.write(chunk)
        return Path(f.name)


def main():
    ap = argparse.ArgumentParser(description="Build static GTFS stop patterns artifact")
    ap.add_argument("--sqlite", type=Path, default=None)
    ap.add_argument("--trips-txt", type=Path, default=DEFAULT_TRIPS_TXT)
    ap.add_argument("--zip", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--min-trip-count", type=int, default=1)
    args = ap.parse_args()

    if args.sqlite:
        print(f"Loading from SQLite {args.sqlite} (+ direction from {args.trips_txt}) ...")
        stops, routes, trips, seqs, transfer_rows = load_from_sqlite(args.sqlite, args.trips_txt)
    else:
        zip_path = args.zip or _download_zip()
        print(f"Loading from zip {zip_path} ...")
        stops, routes, trips, seqs, transfer_rows = load_from_zip(zip_path)
        if not args.zip:
            zip_path.unlink(missing_ok=True)

    print(f"Parsed: stops={len(stops)} routes={len(routes)} trips={len(trips)} trip_seqs={len(seqs)}")
    components = derive_transfer_components(stops, transfer_rows)
    component_ids = set(components.values())
    print(
        f"Transfers: rows={len(transfer_rows)} components="
        f"{len(component_ids)} multi-parent, {len(components)} member stops"
    )
    artifact = build_patterns(
        stops,
        routes,
        trips,
        seqs,
        min_trip_count=args.min_trip_count,
        transfer_components=components,
    )
    have_dir = sum(1 for p in artifact["patterns"] if p["direction_id"] is not None)
    print(
        f"Built: patterns={artifact['pattern_count']} routes={artifact['route_count']} "
        f"stops={artifact['stop_count']} (patterns with direction_id: {have_dir})"
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, separators=(",", ":")))
    size_mb = args.out.stat().st_size / 1e6
    print(f"Wrote {args.out} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
