import csv
from pathlib import Path


data_dir = Path(__file__).parent.parent.parent / "data" / "gtfs_static"

class GTFSStaticData:

    def __init__(self):
        self.stops_by_id = {}
        self.routes_by_id = {}
        self.transfers_by_stop = {}
        self.routes_by_stop = {}
        self.trips_to_routes = {}

        #load all data
        stops = self.__load_csv("stops.txt")
        routes = self.__load_csv("routes.txt")
        transfers = self.__load_csv("transfers.txt")
        trips = self.__load_csv("trips.txt")
        stop_times = self.__load_csv("stop_times.txt")

        #insert data into dictionaries
        self.stops_by_id = self.__buildIdxDict(stops, "stop_id")
        self.routes_by_id = self.__buildIdxDict(routes, "route_id")
        self.transfers_by_stop = self.__buildGroupDict(transfers, "from_stop_id")
        self.trips_to_routes = {trip["trip_id"]: trip["route_id"] for trip in trips}

        #build routes by stop
        for data in stop_times:
            trip_id = data["trip_id"]
            stop_id = data["stop_id"]
            route = self.trips_to_routes[trip_id]
            self.routes_by_stop.setdefault(stop_id, set()).add(route)

    def __load_csv(self, filename: str) -> list:
        with open(data_dir / filename, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)

    def __buildIdxDict(self, data: list, key: str) -> dict:
        result = {}
        for dataset in data:
            result[dataset[key]] = dataset
        return result

    def __buildGroupDict(self, data: list, key: str) -> dict:
        result = {}
        for dataset in data:
            result.setdefault(dataset[key], []).append(dataset)
        return result

    def get_stop_by_name(self, name: str) -> list:
        stops = []
        for stop in self.stops_by_id.values():
            if stop["stop_name"] == name:
                stops.append(stop["stop_id"])
        return stops

    def get_routes_for_stops(self, stop_id):
        routes = set()
        for stop in self.stops_by_id.values():
            if stop["parent_station"] == stop_id:
                child_id = stop["stop_id"]
                routes.update(self.routes_by_stop.get(child_id, set()))
        return routes

    def get_transfers(self, stop_id):
        return self.transfers_by_stop.get(stop_id, [])

#this is for testing only will clean up later
if __name__ == "__main__":
    test = GTFSStaticData()

    print("=== Stop Lookup ===")
    print("Times Sq stop IDs:", test.get_stop_by_name("Times Sq-42 St"))

    print("\n=== Routes for Stations ===")
    print("Routes at Van Cortlandt (101):", test.get_routes_for_stops("101"))
    print("Routes at Times Sq (725):", test.get_routes_for_stops("725"))
    print("Routes at Times Sq (127):", test.get_routes_for_stops("127"))

    print("\n=== Transfers ===")
    print("Transfers from 101:", test.get_transfers("101"))

    print("\n=== Stats ===")
    print("Total stops:", len(test.stops_by_id))
    print("Total routes:", len(test.routes_by_id))
    print("Total stops with routes:", len(test.routes_by_stop))
