/**
 * Public alert-feed facade. Domain behavior is split by responsibility so
 * rendering code and callers retain this stable import path.
 */

export {
  ALERT_LINE_FAMILIES,
  ALERT_ROUTE_TO_FAMILY,
  normalizeAlertRoutes,
  serviceNameForRoutes,
} from "./alert-line-identities";
export type { AlertLineFamily } from "./alert-line-identities";
export {
  compactAlertSummary,
  compactAlertTitle,
  deriveLifecycle,
  leadSentences,
  splitSentences,
} from "./alert-feed-copy";
export {
  normalizeAlertFeedItems,
  normalizeRecentUpdates,
} from "./alert-feed-normalizer";
export {
  groupAlertThreads,
  latestAlertUpdateLabel,
  sortAlertFeedItems,
} from "./alert-feed-threading";
