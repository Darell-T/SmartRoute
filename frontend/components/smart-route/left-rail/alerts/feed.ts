/**
 * Public alert-feed facade. Domain behavior is split by responsibility so
 * rendering code and callers retain this stable import path.
 */

export {
  ALERT_LINE_FAMILIES,
  ALERT_ROUTE_TO_FAMILY,
  normalizeAlertRoutes,
  serviceNameForRoutes,
} from "./line-identities";
export type { AlertLineFamily } from "./line-identities";
export {
  compactAlertSummary,
  compactAlertTitle,
  deriveLifecycle,
  leadSentences,
  splitSentences,
} from "./feed-copy";
export {
  normalizeAlertFeedItems,
  normalizeRecentUpdates,
} from "./feed-normalizer";
export {
  groupAlertThreads,
  sortAlertFeedItems,
} from "./feed-threading";
