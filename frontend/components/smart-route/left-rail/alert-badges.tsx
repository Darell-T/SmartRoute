import { SUBWAY_BULLET_ROUTES } from "@/components/smart-route/train-bullet";
import { BusChip, RouteBullet, RouteBulletGroup } from "./atoms";

type AlertRouteBadgeGroupProps = {
  routeIds: string[];
  limit?: number;
  size?: number;
};

export function AlertRouteBadgeGroup({
  routeIds,
  limit = 2,
  size,
}: AlertRouteBadgeGroupProps) {
  const subwayRoutes = routeIds.filter((routeId) =>
    SUBWAY_BULLET_ROUTES.has(routeId),
  );
  const badgeSize = size ?? 26;

  if (subwayRoutes.length === routeIds.length) {
    return <RouteBulletGroup lines={routeIds} size={badgeSize} limit={limit} />;
  }

  return (
    <span className="sr-alert-route-badges">
      {routeIds.slice(0, limit).map((routeId) => (
        <AlertRouteBadge key={routeId} routeId={routeId} size={badgeSize} />
      ))}
    </span>
  );
}

type AlertRouteBadgeProps = {
  routeId: string;
  size: number;
};

export function AlertRouteBadge({ routeId, size }: AlertRouteBadgeProps) {
  if (SUBWAY_BULLET_ROUTES.has(routeId)) {
    return <RouteBullet line={routeId} size={size} />;
  }

  return <BusChip route={routeId} />;
}
