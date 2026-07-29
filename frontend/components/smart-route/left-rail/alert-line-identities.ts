import { ALL_LINES } from "./types";

const SUBWAY_ORDER = new Map(ALL_LINES.map((line, index) => [line, index]));
const BUS_ROUTE_PATTERN = /^(?:BXM|BM|BX|B|QM|Q|SIM|S|M|X)\d{1,3}[A-Z]?(?:-?SBS)?$/;

type AlertLineRouteDefinition = {
  routeId: string;
  serviceName: string;
  aliases?: string[];
};

export type AlertLineFamily = {
  id: string;
  name: string;
  routeIds: string[];
  rank: number;
  routes: AlertLineRouteDefinition[];
};

export const ALERT_LINE_FAMILIES: AlertLineFamily[] = [
  {
    id: "7-avenue",
    name: "7 Avenue",
    routeIds: ["1", "2", "3"],
    rank: 10,
    routes: [
      { routeId: "1", serviceName: "7 Avenue Local" },
      { routeId: "2", serviceName: "7 Avenue Express" },
      { routeId: "3", serviceName: "7 Avenue Express" },
    ],
  },
  {
    id: "lexington-avenue",
    name: "Lexington Avenue",
    routeIds: ["4", "5", "6"],
    rank: 20,
    routes: [
      { routeId: "4", serviceName: "Lexington Av Express" },
      { routeId: "5", serviceName: "Lexington Av Express" },
      { routeId: "6", serviceName: "Lexington Av Local" },
      { routeId: "6X", serviceName: "Lexington Av Express" },
    ],
  },
  {
    id: "flushing",
    name: "Flushing",
    routeIds: ["7"],
    rank: 30,
    routes: [{ routeId: "7", serviceName: "Flushing Line", aliases: ["7X"] }],
  },
  {
    id: "8-avenue",
    name: "8 Avenue",
    routeIds: ["A", "C", "E"],
    rank: 40,
    routes: [
      { routeId: "A", serviceName: "8 Avenue Express" },
      { routeId: "C", serviceName: "8 Avenue Local" },
      { routeId: "E", serviceName: "8 Avenue Local" },
    ],
  },
  {
    id: "6-avenue",
    name: "6 Avenue",
    routeIds: ["B", "D", "F", "M"],
    rank: 50,
    routes: [
      { routeId: "B", serviceName: "6 Avenue Express" },
      { routeId: "D", serviceName: "6 Avenue Express" },
      { routeId: "F", serviceName: "6 Avenue Local", aliases: ["FX"] },
      { routeId: "M", serviceName: "6 Avenue Local" },
    ],
  },
  {
    id: "crosstown",
    name: "Crosstown",
    routeIds: ["G"],
    rank: 60,
    routes: [{ routeId: "G", serviceName: "Crosstown Line" }],
  },
  {
    id: "nassau-st",
    name: "Nassau St",
    routeIds: ["J", "Z"],
    rank: 70,
    routes: [
      { routeId: "J", serviceName: "Nassau St Line" },
      { routeId: "Z", serviceName: "Nassau St Line" },
    ],
  },
  {
    id: "canarsie",
    name: "Canarsie",
    routeIds: ["L"],
    rank: 80,
    routes: [{ routeId: "L", serviceName: "14 St-Canarsie" }],
  },
  {
    id: "broadway",
    name: "Broadway",
    routeIds: ["N", "Q", "R", "W"],
    rank: 90,
    routes: [
      { routeId: "N", serviceName: "Broadway Express" },
      { routeId: "Q", serviceName: "Broadway Express" },
      { routeId: "R", serviceName: "Broadway Local" },
      { routeId: "W", serviceName: "Broadway Local" },
    ],
  },
  {
    id: "shuttles",
    name: "Shuttles",
    routeIds: ["S"],
    rank: 100,
    routes: [
      { routeId: "S", serviceName: "42 St Shuttle", aliases: ["FS", "GS", "H"] },
    ],
  },
  {
    id: "staten-island-railway",
    name: "Staten Island Railway",
    routeIds: ["SIR"],
    rank: 110,
    routes: [
      { routeId: "SIR", serviceName: "Staten Island Railway", aliases: ["SI"] },
    ],
  },
];

export const ALERT_ROUTE_TO_FAMILY = new Map<string, AlertLineFamily>(
  ALERT_LINE_FAMILIES.flatMap((family) =>
    family.routes.flatMap((route) =>
      [route.routeId, ...(route.aliases ?? [])].map(
        (routeId) => [routeId.toUpperCase(), family] as const,
      ),
    ),
  ),
);

const TRUNK_NAMES: Record<string, string> = Object.fromEntries(
  ALERT_LINE_FAMILIES.flatMap((family) =>
    family.routes.flatMap((route) =>
      [route.routeId, ...(route.aliases ?? [])].map(
        (routeId) => [routeId.toUpperCase(), route.serviceName] as const,
      ),
    ),
  ),
);

export function serviceNameForRoutes(routeIds: string[]): string | undefined {
  const primary = routeIds[0];
  if (!primary) {
    return undefined;
  }

  const trunks = routeIds.map((route) => TRUNK_NAMES[route]);
  if (trunks.length > 0 && trunks.every((name) => name && name === trunks[0])) {
    return trunks[0];
  }
  if (routeIds.length > 1) {
    return "Multiple lines";
  }
  if (BUS_ROUTE_PATTERN.test(primary)) {
    return `${primary} bus`;
  }

  return `${primary} service`;
}

export function normalizeAlertRoutes(
  routes: Array<string | null | undefined>,
): string[] {
  return Array.from(
    new Set(
      routes
        .map((route) => String(route ?? "").trim().toUpperCase())
        .filter(Boolean),
    ),
  ).sort(routeSort);
}

function routeSort(left: string, right: string): number {
  const leftIndex = SUBWAY_ORDER.get(left);
  const rightIndex = SUBWAY_ORDER.get(right);
  if (leftIndex !== undefined || rightIndex !== undefined) {
    return (leftIndex ?? 999) - (rightIndex ?? 999);
  }

  const leftIsBus = BUS_ROUTE_PATTERN.test(left);
  const rightIsBus = BUS_ROUTE_PATTERN.test(right);
  if (leftIsBus !== rightIsBus) {
    return leftIsBus ? 1 : -1;
  }

  return left.localeCompare(right);
}
