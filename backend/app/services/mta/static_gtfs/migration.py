import csv
import io
import os
import tempfile
import zipfile
from pathlib import Path

import httpx
import psycopg2
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
    try:
        conn = psycopg2.connect(DATABASE_URL)
        try:
            cur = conn.cursor()
            try:
                # Close cursor and connection in their own scopes so a cursor
                # creation or close failure never leaks the connection.
                print("Creating tables...")
                cur.execute("""
                    BEGIN;
                    DROP TABLE IF EXISTS stop_times;
                    DROP TABLE IF EXISTS trips;
                    DROP TABLE IF EXISTS stops;
                    DROP TABLE IF EXISTS transfers;

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
                    -- transfers.txt fields stay TEXT so optional or blank values
                    -- (for example min_transfer_time) copy without loss or invention.
                    CREATE TABLE transfers (
                        from_stop_id TEXT,
                        to_stop_id TEXT,
                        transfer_type TEXT,
                        min_transfer_time TEXT
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

                    print("Loading transfers...")
                    with zf.open("transfers.txt") as f:
                        buffer = io.StringIO()
                        reader = csv.DictReader(line.decode() for line in f)
                        for row in reader:
                            buffer.write(f"{row['from_stop_id']}\t{row['to_stop_id']}\t{row.get('transfer_type','')}\t{row.get('min_transfer_time','')}\n")
                        buffer.seek(0)
                        cur.copy_from(buffer, "transfers", columns=("from_stop_id", "to_stop_id", "transfer_type", "min_transfer_time"))
                        print("Transfers loaded")

                print("Building indexes...")
                cur.execute("""
                    CREATE INDEX idx_stop_times_trip ON stop_times(trip_id);
                    CREATE INDEX idx_stop_times_stop ON stop_times(stop_id);
                    CREATE INDEX idx_stops_name ON stops(stop_name);
                    CREATE INDEX idx_trips_route ON trips(route_id);
                    CREATE INDEX idx_transfers_from ON transfers(from_stop_id);
                    COMMIT;
                """)
                print("Migration complete.")
            finally:
                cur.close()
        finally:
            conn.close()
    finally:
        zip_path.unlink()

if __name__ == "__main__":
    migrate()
