export const ROUTE_DESTINATION_FALLBACKS: Record<
  string,
  Partial<Record<"uptown" | "downtown", string>>
> = {
  "1": { uptown: "Van Cortlandt Park-242 St", downtown: "South Ferry" },
  "2": { uptown: "Wakefield-241 St", downtown: "Flatbush Av-Brooklyn College" },
  "3": { uptown: "Harlem-148 St", downtown: "New Lots Av" },
  "4": { uptown: "Woodlawn", downtown: "New Lots Av" },
  "5": { uptown: "Eastchester-Dyre Av", downtown: "Flatbush Av-Brooklyn College" },
  "6": { uptown: "Pelham Bay Park", downtown: "Brooklyn Bridge-City Hall" },
  "7": { uptown: "Flushing-Main St", downtown: "34 St-Hudson Yards" },
  A: { uptown: "Inwood-207 St", downtown: "Far Rockaway-Mott Av" },
  B: { uptown: "Bedford Park Blvd", downtown: "Brighton Beach" },
  C: { uptown: "168 St", downtown: "Euclid Av" },
  D: { uptown: "Norwood-205 St", downtown: "Coney Island-Stillwell Av" },
  E: { uptown: "Jamaica Center-Parsons/Archer", downtown: "World Trade Center" },
  F: { uptown: "Jamaica-179 St", downtown: "Coney Island-Stillwell Av" },
  G: { uptown: "Court Sq", downtown: "Church Av" },
  J: { uptown: "Jamaica Center-Parsons/Archer", downtown: "Broad St" },
  L: { uptown: "8 Av", downtown: "Canarsie-Rockaway Pkwy" },
  M: { uptown: "Forest Hills-71 Av", downtown: "Middle Village-Metropolitan Av" },
  N: { uptown: "Astoria-Ditmars Blvd", downtown: "Coney Island-Stillwell Av" },
  Q: { uptown: "96 St", downtown: "Coney Island-Stillwell Av" },
  R: { uptown: "Forest Hills-71 Av", downtown: "Bay Ridge-95 St" },
  W: { uptown: "Astoria-Ditmars Blvd", downtown: "Whitehall St" },
  Z: { uptown: "Jamaica Center-Parsons/Archer", downtown: "Broad St" },
  SI: { uptown: "St George", downtown: "Tottenville" },
  S: { uptown: "Shuttle", downtown: "Shuttle" },
};

export const ROUTE_SERVICE_PATTERNS: Record<string, string> = {
  "1": "Broadway-7 Av Local",
  "2": "Broadway-7 Av Express",
  "3": "Broadway-7 Av Express",
  "4": "Lexington Av Express",
  "5": "Lexington Av Express",
  "6": "Lexington Av Local",
  "7": "Flushing Local",
  A: "8 Av Express",
  B: "6 Av Express",
  C: "8 Av Local",
  D: "6 Av Express",
  E: "8 Av Local",
  F: "6 Av Local",
  G: "Crosstown Local",
  J: "Nassau St Local",
  L: "14 St-Canarsie Local",
  M: "6 Av Local",
  N: "Broadway Local",
  Q: "Broadway Express",
  R: "Broadway Local",
  W: "Broadway Local",
  Z: "Nassau St Express",
  S: "Shuttle",
  SI: "Staten Island Railway",
};

const SUBWAY_ROUTE_SORT_ORDER = [
  "1", "2", "3",
  "4", "5", "6",
  "7",
  "A", "C", "E",
  "B", "D", "F", "M",
  "G",
  "J", "Z",
  "L",
  "N", "Q", "R", "W",
  "S", "SI",
];

const SUBWAY_ROUTE_SORT_INDEX = new Map(
  SUBWAY_ROUTE_SORT_ORDER.map((routeId, index) => [routeId, index]),
);

export function normalizeRouteId(routeId: string): string {
  const upper = routeId.toUpperCase();
  if (upper === "6X") return "6";
  if (upper === "7X") return "7";
  if (upper === "FX") return "F";
  if (upper === "FS" || upper === "GS" || upper === "H") return "S";
  if (upper === "SIR") return "SI";
  return upper;
}


export function compareRouteId(left: string, right: string): number {
  const leftIndex = SUBWAY_ROUTE_SORT_INDEX.get(left);
  const rightIndex = SUBWAY_ROUTE_SORT_INDEX.get(right);
  if (typeof leftIndex === "number" && typeof rightIndex === "number") {
    return leftIndex - rightIndex;
  }
  if (typeof leftIndex === "number") return -1;
  if (typeof rightIndex === "number") return 1;
  return left.localeCompare(right, undefined, { numeric: true });
}
