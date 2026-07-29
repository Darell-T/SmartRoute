import { SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";
import { ALERT_ROUTE_TO_FAMILY } from "./alert-feed";
import type { AlertFeedItem } from "./types";

const SYSTEMWIDE_ROUTE_THRESHOLD = 8;

export type AlertLineGroupModel = {
  id: string;
  name: string;
  routeIds: string[];
  items: AlertFeedItem[];
  firstIndex: number;
  rank: number;
};

export function isSystemwideAlert(item: AlertFeedItem): boolean {
  return item.routeIds.length >= SYSTEMWIDE_ROUTE_THRESHOLD;
}

export function partitionAlertItems(
  items: AlertFeedItem[],
  nearbyRouteIds: string[],
  featuredLimit: number,
): { featured: AlertFeedItem[]; rest: AlertFeedItem[] } {
  const near = new Set(nearbyRouteIds.map((route) => route.toUpperCase()));
  const eligible = items.filter(
    (item) =>
      !isSystemwideAlert(item) &&
      item.routeIds.some((route) => near.has(route)),
  );
  const ranked = [...eligible].sort((left, right) => {
    const leftNear = left.routeIds.some((route) => near.has(route)) ? 1 : 0;
    const rightNear = right.routeIds.some((route) => near.has(route)) ? 1 : 0;
    return rightNear - leftNear;
  });
  const featured = ranked.slice(0, featuredLimit);
  const selectedIds = new Set(featured.map((item) => item.id));

  return {
    featured,
    rest: items.filter((item) => !selectedIds.has(item.id)),
  };
}

export function groupAlertItemsByLine(
  items: AlertFeedItem[],
): AlertLineGroupModel[] {
  const groups = new Map<string, AlertLineGroupModel>();

  items.forEach((item, index) => {
    const seed = alertLineGroupSeed(item);
    const existing = groups.get(seed.id);
    if (existing) {
      existing.items.push(item);
      existing.routeIds = mergeRouteIds(existing.routeIds, seed.routeIds);
      existing.firstIndex = Math.min(existing.firstIndex, index);
      existing.rank = Math.min(existing.rank, seed.rank);
      return;
    }

    groups.set(seed.id, {
      ...seed,
      routeIds: uniqueRoutes(seed.routeIds),
      items: [item],
      firstIndex: index,
    });
  });

  return Array.from(groups.values()).sort(
    (left, right) => left.rank - right.rank || left.firstIndex - right.firstIndex,
  );
}

function alertLineGroupSeed(
  item: AlertFeedItem,
): Omit<AlertLineGroupModel, "items" | "firstIndex"> {
  const routeIds = uniqueRoutes(item.routeIds);
  if (isSystemwideAlert(item) || routeIds.length === 0) {
    return { id: "systemwide", name: "Systemwide", routeIds, rank: 900 };
  }

  const subwayRoutes = routeIds.filter((routeId) =>
    SUBWAY_BULLET_ROUTES.has(routeId),
  );
  const busRoutes = routeIds.filter(
    (routeId) => !SUBWAY_BULLET_ROUTES.has(routeId),
  );
  const families = uniqueFamilyIds(subwayRoutes);
  if (families.length === 1 && busRoutes.length === 0) {
    const family = ALERT_ROUTE_TO_FAMILY.get(subwayRoutes[0]);
    if (family) {
      return {
        id: family.id,
        name: family.name,
        routeIds: family.routeIds,
        rank: family.rank,
      };
    }
  }
  if (subwayRoutes.length > 0) {
    return {
      id: "multiple-lines",
      name: "Multiple lines",
      routeIds,
      rank: 800,
    };
  }

  const primaryRoute = busRoutes[0] ?? routeIds[0];
  return {
    id: `bus-${primaryRoute}`,
    name: `${primaryRoute} bus`,
    routeIds: [primaryRoute],
    rank: 700,
  };
}

function uniqueFamilyIds(routeIds: string[]): string[] {
  return Array.from(
    new Set(
      routeIds
        .map((routeId) => ALERT_ROUTE_TO_FAMILY.get(routeId)?.id)
        .filter((id): id is string => Boolean(id)),
    ),
  );
}

function uniqueRoutes(routeIds: string[]): string[] {
  return Array.from(
    new Set(routeIds.map((routeId) => routeId.trim().toUpperCase())),
  )
    .filter(Boolean)
    .sort(routeSortRank);
}

function mergeRouteIds(left: string[], right: string[]): string[] {
  return uniqueRoutes([...left, ...right]);
}

function routeSortRank(left: string, right: string): number {
  const leftFamily = ALERT_ROUTE_TO_FAMILY.get(left);
  const rightFamily = ALERT_ROUTE_TO_FAMILY.get(right);
  if (leftFamily || rightFamily) {
    const leftRank = leftFamily
      ? leftFamily.rank * 10 + familyRoutePosition(leftFamily, left)
      : 9999;
    const rightRank = rightFamily
      ? rightFamily.rank * 10 + familyRoutePosition(rightFamily, right)
      : 9999;
    return leftRank - rightRank;
  }

  if (SUBWAY_BULLET_ROUTES.has(left) !== SUBWAY_BULLET_ROUTES.has(right)) {
    return SUBWAY_BULLET_ROUTES.has(left) ? -1 : 1;
  }

  return left.localeCompare(right);
}

function familyRoutePosition(
  family: { routeIds: string[] },
  routeId: string,
): number {
  const index = family.routeIds.indexOf(routeId);
  return index >= 0 ? index : family.routeIds.length;
}
