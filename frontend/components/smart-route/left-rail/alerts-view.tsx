"use client";

import { Navigation } from "lucide-react";
import { useMemo } from "react";
import {
  latestAlertUpdateLabel,
  normalizeAlertFeedItems,
} from "./alert-feed";
import { AlertCard } from "./alert-featured-card";
import { AlertEmptyState, AlertLineGroupList } from "./alert-line-list";
import {
  groupAlertItemsByLine,
  partitionAlertItems,
} from "./alert-view-model";
import type { FeedEvent, ServiceAlert } from "./types";

const FEATURED_LIMIT = 2;

type AlertsViewProps = {
  alerts: ServiceAlert[];
  feed: FeedEvent[];
  nearbyRouteIds?: string[];
};

export function AlertsView({
  alerts,
  feed,
  nearbyRouteIds = [],
}: AlertsViewProps) {
  const items = useMemo(
    () =>
      normalizeAlertFeedItems(alerts, feed).filter(
        (item) => item.lifecycle !== "resolved",
      ),
    [alerts, feed],
  );
  const updatedLabel = useMemo(() => latestAlertUpdateLabel(items), [items]);
  const { featured, rest } = useMemo(
    () => partitionAlertItems(items, nearbyRouteIds, FEATURED_LIMIT),
    [items, nearbyRouteIds],
  );
  const otherAlertGroups = useMemo(() => groupAlertItemsByLine(rest), [rest]);

  return (
    <section className="sr-alerts-panel">
      <section className="sr-rail-section sr-alerts-header">
        <h1 className="sr-rail-title">Service alerts</h1>
        {updatedLabel && (
          <p className="sr-alerts-updated">
            <Navigation
              className="sr-alerts-updated__icon"
              size={12}
              strokeWidth={0}
              fill="currentColor"
              aria-hidden="true"
            />
            {updatedLabel}
          </p>
        )}
      </section>
      <section className="sr-rail-section sr-alert-feed">
        <div className="sr-alerts-scroll">
          {featured.length > 0 && (
            <section className="sr-alert-group">
              <div className="sr-section-header">
                <h2>Near you</h2>
              </div>
              <ul className="sr-alert-card-list" aria-label="Alerts near you">
                {featured.map((item) => (
                  <AlertCard key={item.id} item={item} />
                ))}
              </ul>
            </section>
          )}
          {rest.length > 0 && (
            <section className="sr-alert-group">
              {featured.length > 0 && (
                <div className="sr-section-header">
                  <h2>Other alerts</h2>
                </div>
              )}
              <AlertLineGroupList
                groups={otherAlertGroups}
                aria-label={
                  featured.length > 0
                    ? "Other service alerts"
                    : "All service alerts"
                }
              />
            </section>
          )}
          {items.length === 0 && <AlertEmptyState />}
        </div>
      </section>
    </section>
  );
}
