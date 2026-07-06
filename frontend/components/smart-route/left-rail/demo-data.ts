/* ════════════════════════════════════════════════════════════════════════
   SmartRoute — Left Rail demo data

   Sample payloads used by the standalone story/demo entry point. Mirrors
   the prototype's data shape exactly so visual fidelity can be A/B'd against
   the HTML mockup. Replace these with live data from the agent pipeline in
   the production wiring.
   ════════════════════════════════════════════════════════════════════════ */

import type {
  Arrival,
  FeedEvent,
  IssueItem,
  NearbyGroupedArrival,
  NearbyTransitGroup,
  NetworkHealth,
  RoutePlan,
  ServiceAlert,
  Station,
} from "./types";

export const DEMO_STATION: Station = {
  name: "34 St-Herald Sq",
  walk: "3 min walk",
  dist: "230 m",
  updatedSec: 26,
};

export const DEMO_HEALTH: NetworkHealth = {
  status: "disrupted",
  alerts: 5,
  lines: 4,
  major: 3,
  stale: 34,
  summary:
    "5 subway alerts are active across 4 lines. 34 trains are reporting stale positions, so headways may wobble a bit.",
  affected: ["A", "C", "D", "F"],
};

const DEMO_ARRIVAL_FIXTURES = [
  { line: "R", way: "uptown", dest: "Forest Hills", label: "Now", mins: 0, status: "On Time", stale: false },
  { line: "Q", way: "uptown", dest: "96 St", label: "1 min", mins: 1, status: "On Time", stale: false },
  { line: "W", way: "uptown", dest: "Ditmars Blvd", label: "3 min", mins: 3, status: "On Time", stale: false },
  { line: "N", way: "uptown", dest: "Ditmars Blvd", label: "6 min", mins: 6, status: "On Time", stale: true },
  { line: "R", way: "uptown", dest: "Forest Hills", label: "9 min", mins: 9, status: "On Time", stale: false },
  { line: "R", way: "downtown", dest: "Bay Ridge–95 St", label: "2 min", mins: 2, status: "On Time", stale: false },
  { line: "Q", way: "downtown", dest: "Coney Island", label: "4 min", mins: 4, status: "On Time", stale: false },
  { line: "W", way: "downtown", dest: "Whitehall St", label: "7 min", mins: 7, status: "Delayed", stale: false },
  { line: "N", way: "downtown", dest: "Coney Island", label: "9 min", mins: 9, status: "On Time", stale: true },
  { line: "B63", mode: "bus", way: "unknown", dest: "Downtown Brooklyn", label: "4 min", mins: 4, status: "On Time", stale: false },
  { line: "M34", mode: "bus", way: "unknown", dest: "Javits Center", label: "7 min", mins: 7, status: "On Time", stale: false },
];

function demoArrival(
  arrival: (typeof DEMO_ARRIVAL_FIXTURES)[number],
): Arrival {
  const isBus = "mode" in arrival && arrival.mode === "bus";
  const servicePattern =
    isBus
      ? undefined
      : arrival.line === "Q"
      ? "Broadway Express"
      : ["N", "R", "W"].includes(arrival.line)
        ? "Broadway Local"
        : undefined;
  const destination =
    isBus
      ? arrival.dest
      : arrival.dest === "Ditmars Blvd"
      ? "Astoria-Ditmars Blvd"
      : arrival.dest === "Forest Hills"
        ? "Forest Hills-71 Av"
        : arrival.dest === "Coney Island"
          ? "Coney Island-Stillwell Av"
          : arrival.dest;
  const stopName = isBus ? "5 Av / W 34 St" : "34 St-Herald Sq";
  const walkMinutes = isBus ? 2 : 3;
  const distanceMiles = 0.1;
  const stationDistanceM = isBus ? 160 : 230;

  return {
    ...arrival,
    id: `demo-${arrival.line}-${arrival.way}-${destination}`,
    mode: isBus ? "bus" : "subway",
    routeIds: [arrival.line],
    destination,
    servicePattern,
    stopName,
    walkMinutes,
    distanceMiles,
    arrivalMinutes: [arrival.mins],
    direction: arrival.way as Arrival["direction"],
    predictionType: arrival.stale ? "scheduled" : "live",
    predictionFreshness: arrival.stale ? "scheduled" : "fresh",
    alertSeverity: arrival.status === "Delayed" ? "minor" : "none",
    way: arrival.way as Arrival["direction"],
    dest: destination,
    status: arrival.status as Arrival["status"],
    stationName: stopName,
    stationDistanceM,
  };
}

export const DEMO_ARRIVALS: Arrival[] = DEMO_ARRIVAL_FIXTURES.map(demoArrival);

function groupedArrivalFromDemo(bucket: Arrival[]): NearbyGroupedArrival {
  const first = bucket[0];
  const arrivalMinutes = Array.from(
    new Set(bucket.flatMap((arrival) => arrival.arrivalMinutes)),
  ).sort((left, right) => left - right);
  return {
    id: first.id,
    mode: first.mode,
    routeIds: first.routeIds,
    destination: first.destination,
    servicePattern: first.servicePattern,
    stopName: first.stopName,
    walkMinutes: first.walkMinutes,
    distanceMiles: first.distanceMiles,
    arrivalMinutes,
    direction: first.direction,
    predictionType: bucket.some((arrival) => arrival.predictionType === "live")
      ? "live"
      : "scheduled",
    predictionFreshness: bucket.some(
      (arrival) => arrival.predictionFreshness === "fresh",
    )
      ? "fresh"
      : first.predictionFreshness,
    alertSeverity: bucket.some((arrival) => arrival.alertSeverity === "minor")
      ? "minor"
      : first.alertSeverity,
  };
}

function buildDemoStationGroups(arrivals: Arrival[]): NearbyTransitGroup[] {
  const subwayRows = arrivals.filter((arrival) => arrival.mode === "subway");
  const groupedByService = new Map<string, Arrival[]>();
  for (const arrival of subwayRows) {
    const key = [
      arrival.routeIds[0],
      arrival.direction,
      arrival.destination,
      arrival.servicePattern ?? "",
    ].join("|");
    const bucket = groupedByService.get(key) ?? [];
    bucket.push(arrival);
    groupedByService.set(key, bucket);
  }

  return [
    {
      id: "demo-34-st-herald-sq",
      name: "34 St-Herald Sq",
      mode: "subway",
      routeIds: ["N", "Q", "R", "W"],
      walkMinutes: 3,
      distanceMiles: 0.1,
      arrivals: Array.from(groupedByService.values())
        .map(groupedArrivalFromDemo)
        .sort(
          (left, right) =>
            (left.arrivalMinutes[0] ?? 99) - (right.arrivalMinutes[0] ?? 99),
        ),
    },
  ];
}

export const DEMO_NEARBY_TRANSIT_GROUPS: NearbyTransitGroup[] =
  buildDemoStationGroups(DEMO_ARRIVALS);

export const DEMO_NEARBY_BUS_ARRIVALS: Arrival[] = DEMO_ARRIVALS.filter(
  (arrival) => arrival.mode === "bus",
);

export const DEMO_ROUTE: RoutePlan = {
  headline: "Take the Q from DeKalb.",
  rationale:
    "Fastest available option · live arrival in 4 min · no service alerts.",
  headsign: "96 St",
  eta: "5:48 PM",
  totalTime: "23 min",
  leaveByLabel: "5:26 PM",
  transferCount: 0,
  strip: [
    { kind: "walk", minutes: 3 },
    { kind: "ride", routeId: "Q", mode: "subway" },
    { kind: "walk", minutes: 2 },
  ],
  detailSteps: [
    {
      kind: "walk",
      title: "Walk to 34 St-Herald Sq station",
      subtitle: "About 3 min",
    },
    {
      kind: "board",
      routeId: "Q",
      mode: "subway",
      title: "Board the Q train",
      subtitle: "Manhattan-bound to 96 St",
      note: "Departs in 4 min",
      live: true,
    },
    {
      kind: "ride",
      routeId: "Q",
      mode: "subway",
      title: "Ride the Q",
      fromStop: "34 St-Herald Sq",
      toStop: "86 St",
      rideMeta: "Ride 5 stops · 16 min",
    },
    { kind: "walk", title: "Walk to destination", subtitle: "About 2 min" },
  ],
  pickedLine: "Q",
  steps: [
    {
      type: "walk",
      action: "Walk",
      title: "Walk",
      detail: "To 34 St-Herald Sq",
      duration: "3 min",
    },
    {
      type: "board",
      action: "Board",
      line: "Q",
      title: "Q train",
      detail: "96 St",
      note: "Departs in 4 min",
      live: true,
      duration: "now",
    },
    {
      type: "ride",
      action: "Ride",
      title: "16 min, 5 stops",
      detail: "42 St · 57 St · Lex Av/63 · 72 St · 86 St",
      duration: "16 min",
    },
    {
      type: "exit",
      action: "Exit",
      title: "86 St",
      detail: "Front of train · 2nd Av exit",
      duration: "1 min",
    },
    {
      type: "destination",
      action: "Arrive",
      title: "Atlantic Terminal",
      detail: "180 m via 86 St & 2nd Av",
      duration: "2 min",
    },
  ],
  alternatives: [
    {
      id: "demo-alt-b",
      line: "B",
      dest: "145 St",
      delta: "+3 min",
      reason: "Affected by delays near 36 St",
      sev: "high",
      status: "rejected",
      lines: ["B"],
      totalMinutes: 26,
      departsInMinutes: 7,
      leavesLabel: "5:32 PM",
      arriveLabel: "5:51 PM",
      fromStop: "34 St-Herald Sq",
      toStop: "145 St",
      strip: [
        { kind: "walk", minutes: 4 },
        { kind: "ride", routeId: "B", mode: "subway" },
        { kind: "walk", minutes: 6 },
      ],
    },
    {
      id: "demo-alt-r",
      line: "R",
      dest: "Forest Hills",
      delta: "+8 min ride",
      reason: "Slower local · same start",
      sev: "low",
      status: "rejected",
      lines: ["R"],
      totalMinutes: 31,
      departsInMinutes: 4,
      leavesLabel: "5:29 PM",
      arriveLabel: "5:56 PM",
      fromStop: "34 St-Herald Sq",
      toStop: "Forest Hills-71 Av",
      strip: [
        { kind: "walk", minutes: 2 },
        { kind: "ride", routeId: "R", mode: "subway" },
        { kind: "walk", minutes: 5 },
      ],
    },
    {
      id: "demo-alt-n",
      line: "N",
      dest: "Astoria",
      delta: "stale data",
      reason: "No live arrival data right now",
      sev: "medium",
      status: "rejected",
      lines: ["N"],
      totalMinutes: 29,
      departsInMinutes: 9,
      leavesLabel: "5:34 PM",
      arriveLabel: "5:54 PM",
      fromStop: "34 St-Herald Sq",
      toStop: "Astoria-Ditmars Blvd",
      strip: [
        { kind: "walk", minutes: 2 },
        { kind: "ride", routeId: "N", mode: "subway" },
        { kind: "walk", minutes: 5 },
      ],
    },
  ],
  notes: [
    { tone: "cyan", t: "Q line", v: "on-time across all 14 stations" },
    { tone: "cyan", t: "Stations", v: "no incidents within 0.3 mi" },
    { tone: "sage", t: "Nearby", v: "no recent incidents near this route" },
    { tone: "amber", t: "Weather", v: "72°F · clear · 0% precip in 30 min" },
  ],
};

export const DEMO_FEED: FeedEvent[] = [
  { src: "MTA", sev: "major", line: "D", title: "Partial suspension · 36 St ↔ Atlantic", time: "6m", detail: "Signal problem. Shuttle bus on the affected segment." },
  { src: "FEED", sev: "minor", line: null, title: "Fire response · Canal St", time: "11m", detail: "≈ 220 ft from J/Z entrance. @NYCrimeNow." },
  { src: "MTA", sev: "minor", line: "F", title: "Delays · northbound", time: "12m", detail: "Sick passenger at 47–50 Sts." },
  { src: "FEED", sev: "watch", line: "2", title: "Stalled · 2 train", time: "6m", detail: "No GTFS-RT update between Hoyt and Nevins." },
  { src: "MTA", sev: "minor", line: "L", title: "Minor delays · Brooklyn-bound", time: "18m", detail: "Earlier signal issue at Bedford clearing." },
  { src: "FEED", sev: "watch", line: null, title: "Medical · 14 St-Union Sq mezzanine", time: "22m", detail: "FDNY on scene. @CitizenAppNYC." },
  { src: "MTA", sev: "planned", line: "A", title: "Weekend service change", time: "2h", detail: "No Far Rockaway-bound trains btwn Lefferts and Howard Beach." },
  { src: "FEED", sev: "watch", line: "D", title: "Stalled · D train", time: "8m", detail: "No update between 9 St and 4 Av." },
  { src: "FEED", sev: "watch", line: null, title: "Police activity · 125 St", time: "38m", detail: "Two cruisers on Lenox Av side. Unverified." },
];

export const DEMO_LINE_STATE: Record<string, "major" | "minor" | "planned"> = {
  D: "major",
  F: "minor",
  A: "planned",
  C: "planned",
  L: "minor",
};

export const DEMO_ALERTS: ServiceAlert[] = [
  {
    sev: "major",
    kind: "train",
    lines: ["B", "D"],
    title: "Coney Island–Stillwell Av-bound B / D trains",
    sub: "Running with delays",
    aiContext:
      "Signal failure near Avenue N is causing southbound B and D delays. Expect elevated wait times for the next 30–45 minutes while crews restore normal service.",
    confidence: "high",
    affectedStops: ["Avenue N", "Kings Hwy", "Avenue P", "Avenue U", "86 St"],
    direction: "Southbound",
    estClear: "~ 11:00 PM",
    startedAgo: "43m ago",
    lastUpdate: "Just now",
    activity: [
      { t: "Just now", e: "Delays continuing" },
      { t: "23m ago", e: "Signal issue reported" },
      { t: "43m ago", e: "Delays began" },
    ],
  },
  {
    sev: "minor",
    kind: "train",
    lines: ["7"],
    title: "Flushing-bound 7 train",
    sub: "Skips 51 St–Corona Plaza",
    aiContext:
      "Track work between Junction Blvd and Mets-Willets Pt has the 7 skipping 51 St. Affects local riders during off-peak hours.",
    confidence: "high",
    affectedStops: ["51 St–Corona Plaza"],
    direction: "Flushing-bound",
    estClear: "—",
    startedAgo: "Apr 20",
    lastUpdate: "just now",
  },
  {
    sev: "minor",
    kind: "train",
    lines: ["7"],
    title:
      "Manhattan-bound 7 skips 69 St and 52 St at 61 St-Woodside",
    sub:
      "Skips 69 St and 52 St; ALL trains at 61 St board from Flushing-bound platform",
    aiContext:
      "Platform construction at 61 St-Woodside. Manhattan-bound trains skipping two stations; board the Flushing-bound platform for service in either direction.",
    confidence: "high",
    affectedStops: ["69 St", "52 St", "61 St-Woodside"],
    direction: "Manhattan-bound",
    estClear: "—",
    startedAgo: "Dec 17",
    lastUpdate: "just now",
  },
  {
    sev: "minor",
    kind: "train",
    lines: ["7"],
    title: "Flushing-bound 7X / 7 trains",
    sub: "Local in Queens from 74 St-Broadway to Mets-Willets Point",
    aiContext:
      "7X runs local for the rest of evening. Add ~6 min to express-route trips.",
    confidence: "high",
    affectedStops: ["74 St-Broadway", "82 St", "90 St", "103 St", "111 St"],
    direction: "Flushing-bound",
    estClear: "—",
    startedAgo: "52m ago",
    lastUpdate: "just now",
  },
  {
    sev: "planned",
    kind: "train",
    lines: ["A"],
    title: "Weekend service change",
    sub: "No Far Rockaway-bound A trains btwn Lefferts and Howard Beach",
    aiContext:
      "Track maintenance Sat–Sun. Shuttle bus from Lefferts Blvd to Howard Beach for the duration.",
    confidence: "high",
    affectedStops: ["Lefferts Blvd", "Howard Beach-JFK"],
    direction: "Far Rockaway-bound",
    estClear: "Mon 5 AM",
    startedAgo: "effective Sat",
    lastUpdate: "just now",
  },
  {
    sev: "minor",
    kind: "train",
    lines: ["2", "3"],
    title: "2 / 3 trains are running with delays in both directions",
    sub: "Signal problem near Franklin Av-Medgar Evers College.",
    aiContext:
      "Crews are investigating a signal problem near Franklin Av. Use 4/5 service where possible.",
    confidence: "high",
    affectedStops: ["Franklin Av-Medgar Evers College", "Nevins St"],
    direction: "Both directions",
    startedAgo: "52m ago",
    lastUpdate: "13m ago",
    activity: [
      { t: "17m ago", e: "Signal problem addressed" },
      { t: "33m ago", e: "Delays first reported" },
      { t: "52m ago", e: "Investigating signal problem near Franklin Av" },
    ],
  },
  {
    sev: "minor",
    kind: "train",
    lines: ["R"],
    title: "R trains have resumed normal service",
    sub: "Earlier signal problem at 36 St resolved.",
    aiContext: "Service has returned to normal after an earlier signal problem at 36 St.",
    confidence: "high",
    startedAgo: "2h ago",
    lastUpdate: "1h ago",
    activity: [
      { t: "1h ago", e: "Service resumed to normal" },
      { t: "2h ago", e: "Signal problem reported at 36 St" },
    ],
  },
];

export const DEMO_ISSUES: IssueItem[] = [
  {
    id: "d-suspension",
    title: "2 Issues",
    detail: "D · 36 St ↔ Atlantic",
  },
];

// Bundled rail data for the standalone story (`app/dev/left-rail`). The
// production app builds this exact shape from live data via
// buildLeftRailData(); the story passes these fixtures so the rail never has
// to fall back to demo values on its own.
export const DEMO_RAIL_DATA = {
  station: DEMO_STATION,
  health: DEMO_HEALTH,
  arrivals: DEMO_ARRIVALS,
  nearbyTransitGroups: DEMO_NEARBY_TRANSIT_GROUPS,
  nearbyBusArrivals: DEMO_NEARBY_BUS_ARRIVALS,
  plan: DEMO_ROUTE,
  feed: DEMO_FEED,
  lineState: DEMO_LINE_STATE,
  alerts: DEMO_ALERTS,
  issues: DEMO_ISSUES,
};

const THINKING_PHRASES: string[] = [
  "Pulling routes from Google…",
  "Reading the live MTA feed…",
  "Checking which trains are actually moving…",
  "Checking service changes near your stations...",
  "Reviewing service alerts on the relevant lines…",
  "Weighing transfers and walk times…",
  "Ranking route options...",
];

/* Used by the Arrival accordion to build the "next 5 on the {line}" panel.
   Headways are typical NYC peak figures, NOT live data. The production rail
   should derive these from GTFS-RT. */
const HEADWAYS: Record<string, number> = {
  Q: 7, R: 9, W: 11, N: 13, B: 8, D: 10, F: 12, M: 11, A: 8, C: 14, E: 10,
  L: 5, "1": 4, "2": 6, "3": 7, "4": 5, "5": 7, "6": 4, "7": 4, G: 12,
  J: 10, Z: 14, S: 8,
};

export function nextFiveOnLine(arrival: Arrival) {
  const headway = HEADWAYS[arrival.line] ?? 9;
  const tracks =
    arrival.way === "uptown"
      ? ["T2", "T2", "T1", "T2", "T2"]
      : ["T3", "T3", "T4", "T3", "T3"];
  const cars: number[] = [10, 10, 8, 10, 8];
  const crowd: Array<"light" | "moderate" | "heavy"> = [
    "light",
    "moderate",
    "light",
    "moderate",
    "heavy",
  ];
  return Array.from({ length: 5 }, (_, i) => {
    const m = arrival.mins + i * headway;
    return {
      label: m === 0 ? "Now" : `${m} min`,
      mins: m,
      track: tracks[i],
      cars: cars[i],
      crowd: crowd[i],
      stale: i === 0 ? arrival.stale : i === 3,
    };
  });
}
