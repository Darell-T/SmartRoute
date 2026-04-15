import csv
import zipfile
import tempfile
import io
from pathlib import Path
import httpx
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

SUPPLEMENTED_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_supplemented.zip"
DATABASE_URL = os.getenv("DATABASE_URL")

def download_gtfs() -> Path:
    print("Downloading GTFS zip...")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as f:
        with httpx.stream("GET", SUPPLEMENTED_URL, timeout=60, follow_redirects=True) as resp:
            for chunk in resp.iter_bytes():
                f.write(chunk)
        return Path(f.name)

def migrate():
    zip_path = download_gtfs()
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    print("Creating tables...")
    cur.execute("""
        BEGIN;
        DROP TABLE IF EXISTS stop_times;
        DROP TABLE IF EXISTS trips;
        DROP TABLE IF EXISTS stops;
        
        CREATE TABLE stops (
            stop_id TEXT PRIMARY KEY,
            stop_name TEXT,
            stop_lat REAL,
            stop_lon REAL,
            parent_station TEXT,
            location_type TEXT
        );
        CREATE TABLE trips (
            trip_id TEXT PRIMARY KEY,
            route_id TEXT
        );
        CREATE TABLE stop_times (
            trip_id TEXT,
            stop_id TEXT,
            stop_sequence INTEGER
        );
    """)

    with zipfile.ZipFile(zip_path) as zf:
        print("Loading stops...")
        with zf.open("stops.txt") as f:
            buffer = io.StringIO()
            reader = csv.DictReader(line.decode() for line in f)
            for row in reader:
                buffer.write(f"{row['stop_id']}\t{row['stop_name']}\t{row['stop_lat']}\t{row['stop_lon']}\t{row.get('parent_station','')}\t{row.get('location_type','')}\n")
            buffer.seek(0)
            cur.copy_from(buffer, "stops", columns=("stop_id", "stop_name", "stop_lat", "stop_lon", "parent_station", "location_type"))
            print("Stops loaded")

        print("Loading trips...")
        with zf.open("trips.txt") as f:
            buffer = io.StringIO()
            reader = csv.DictReader(line.decode() for line in f)
            for row in reader:
                buffer.write(f"{row['trip_id']}\t{row['route_id']}\n")
            buffer.seek(0)
            cur.copy_from(buffer, "trips", columns=("trip_id", "route_id"))
            print("Trips loaded")

        print("Loading stop_times...")
        with zf.open("stop_times.txt") as f:
            buffer = io.StringIO()
            reader = csv.DictReader(line.decode() for line in f)
            for row in reader:
                buffer.write(f"{row['trip_id']}\t{row['stop_id']}\t{row['stop_sequence']}\n")
            buffer.seek(0)
            cur.copy_from(buffer, "stop_times", columns=("trip_id", "stop_id", "stop_sequence"))
            print("Stop times loaded")

    print("Building indexes...")
    cur.execute("""
        CREATE INDEX idx_stop_times_trip ON stop_times(trip_id);
        CREATE INDEX idx_stop_times_stop ON stop_times(stop_id);
        CREATE INDEX idx_stops_name ON stops(stop_name);
        CREATE INDEX idx_trips_route ON trips(route_id);
        COMMIT;
    """)

    cur.close()
    conn.close()
    zip_path.unlink()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()