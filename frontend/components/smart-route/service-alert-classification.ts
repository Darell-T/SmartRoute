import type { ServiceAlertDetail } from "@/types";
import {
  alertDetail,
  alertTitle,
  normalizeRoutes,
} from "@/components/smart-route/service-alerts-card";

export type ServiceAlertBucketId = "ruiners" | "detours" | "pain";
export type ServiceAlertTone = "major" | "detour" | "watch";

export interface ClassifiedServiceAlert {
  id: string;
  alert: ServiceAlertDetail;
  title: string;
  detail: string;
  routes: string[];
  bucket: ServiceAlertBucketId;
  tone: ServiceAlertTone;
  statusLabel: string;
  affecting: string;
  startedLabel: string;
  severityRank: number;
}

export interface ServiceAlertGroup {
  id: ServiceAlertBucketId;
  label: string;
  tone: ServiceAlertTone;
  items: ClassifiedServiceAlert[];
}

const ROUTE_ORDER = [
  "1",
  "2",
  "3",
  "4",
  "5",
  "6",
  "6X",
  "7",
  "7X",
  "A",
  "C",
  "E",
  "B",
  "D",
  "F",
  "FX",
  "M",
  "G",
  "J",
  "Z",
  "L",
  "N",
  "Q",
  "R",
  "W",
  "S",
  "FS",
  "GS",
  "H",
  "SI",
];

const BUCKET_META: Record<ServiceAlertBucketId, Omit<ServiceAlertGroup, "items">> = {
  ruiners: { id: "ruiners", label: "ROUTE RUNNERS", tone: "major" },
  detours: { id: "detours", label: "PLAN DETOURS", tone: "detour" },
  pain: {
    id: "pain",
    label: "WATCH OUT",
    tone: "watch",
  },
};

const MAJOR_TERMS = [
  "suspend",
  "no service",
  "major delay",
  "signal problem",
  "track condition",
  "emergency",
  "stalled",
  "part suspended",
  "delays are extensive",
];

const DETOUR_TERMS = [
  "rerout",
  "shuttle",
  "bypass",
  "skip-stop",
  "planned work",
  "station closed",
  "runs via",
  "via the",
  "express to local",
  "local to express",
  "modified service",
];

function normalizedText(alert: ServiceAlertDetail) {
  return `${alertTitle(alert)} ${alertDetail(alert)}`.toLowerCase();
}

function firstRouteRank(routes: string[]) {
  const first = routes[0]?.toUpperCase() ?? "";
  const rank = ROUTE_ORDER.indexOf(first);
  return rank === -1 ? ROUTE_ORDER.length : rank;
}

function classifyBucket(alert: ServiceAlertDetail, routes: string[]): ServiceAlertBucketId {
  const text = normalizedText(alert);
  if (MAJOR_TERMS.some((term) => text.includes(term))) return "ruiners";
  if (DETOUR_TERMS.some((term) => text.includes(term))) return "detours";
  if (routes.some((route) => route === "F" || route === "G")) return "pain";
  return "pain";
}

function statusForBucket(bucket: ServiceAlertBucketId, alert: ServiceAlertDetail) {
  const text = normalizedText(alert);
  if (bucket === "ruiners") {
    return { tone: "major" as const, label: "Major Disruption", severityRank: 0 };
  }
  if (bucket === "detours") {
    return {
      tone: "detour" as const,
      label: text.includes("planned") || text.includes("maintenance")
        ? "Planned Work"
        : "Service Change",
      severityRank: 1,
    };
  }
  return {
    tone: "watch" as const,
    label: text.includes("delay") ? "Minor Delay" : "Watch Closely",
    severityRank: 2,
  };
}

function affectingForAlert(alert: ServiceAlertDetail, detail: string) {
  const text = detail.toLowerCase();
  if (text.includes("northbound") || text.includes("uptown")) return "Uptown / northbound";
  if (text.includes("southbound") || text.includes("downtown")) return "Downtown / southbound";
  if (text.includes("both directions")) return "Both directions";
  if ((alert.stop_ids?.length ?? 0) > 0) {
    return `${alert.stop_ids?.length ?? 0} stops`;
  }
  return "Affected route";
}

function formatAlertTime(unix?: number | null) {
  if (!unix) return "Ongoing";
  return new Date(unix * 1000).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function formatAlertDay(unix: number) {
  return new Date(unix * 1000).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

function isMultiDayWindow(start?: number | null, end?: number | null) {
  if (!start || !end) return false;
  return new Date(start * 1000).toDateString() !== new Date(end * 1000).toDateString();
}

export function relativeTimeSince(unix?: number | null) {
  if (!unix) return "Active now";
  const delta = Math.max(0, Math.floor(Date.now() / 1000) - unix);
  if (delta < 60) return "Just now";
  const minutes = Math.floor(delta / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function startedLabelForAlert(alert: ServiceAlertDetail) {
  if (isMultiDayWindow(alert.start, alert.end) && alert.start && alert.end) {
    return `${formatAlertDay(alert.start)} - ${formatAlertDay(alert.end)}`;
  }
  return relativeTimeSince(alert.start);
}

export function classifyServiceAlerts(alerts: ServiceAlertDetail[]): ClassifiedServiceAlert[] {
  return alerts
    .map((alert, index) => {
      const routes = normalizeRoutes(alert);
      const title = alertTitle(alert);
      const detail = alertDetail(alert);
      if (!routes.length && !title && !detail) return null;
      const bucket = classifyBucket(alert, routes);
      const status = statusForBucket(bucket, alert);

      return {
        id: alert.alert_id || `${routes.join("-") || "system"}-${alert.start || index}`,
        alert,
        title: title || "MTA service alert",
        detail,
        routes,
        bucket,
        tone: status.tone,
        statusLabel: status.label,
        affecting: affectingForAlert(alert, detail),
        startedLabel: startedLabelForAlert(alert),
        severityRank: status.severityRank,
      };
    })
    .filter((item): item is ClassifiedServiceAlert => Boolean(item))
    .sort((a, b) => {
      const bucketRank = ["ruiners", "detours", "pain"].indexOf(a.bucket) -
        ["ruiners", "detours", "pain"].indexOf(b.bucket);
      if (bucketRank !== 0) return bucketRank;
      const routeRank = firstRouteRank(a.routes) - firstRouteRank(b.routes);
      if (routeRank !== 0) return routeRank;
      return (b.alert.start || 0) - (a.alert.start || 0);
    });
}

function groupServiceAlerts(alerts: ServiceAlertDetail[]): ServiceAlertGroup[] {
  const classified = classifyServiceAlerts(alerts);
  return (Object.keys(BUCKET_META) as ServiceAlertBucketId[])
    .map((bucket) => ({
      ...BUCKET_META[bucket],
      items: classified.filter((item) => item.bucket === bucket),
    }))
    .filter((group) => group.items.length > 0);
}
