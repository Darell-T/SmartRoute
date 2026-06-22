"use client";

import type { CSSProperties } from "react";
import { AlertOctagon, MinusCircle } from "lucide-react";
import type { ServiceAlertDetail } from "@/types";
import { TrainBullet } from "@/components/smart-route/train-bullet";
import {
  alertDetail,
  categoryForAlert,
  normalizeRoutes,
  relativeAlertTime,
  type Category,
} from "@/components/smart-route/service-alerts-card";
import { Eyebrow } from "./eyebrow";

interface Props {
  alerts: ServiceAlertDetail[];
  maxItems?: number;
  nearestStopName?: string | null;
}

const CATEGORY_TITLE: Record<Category, string> = {
  delays: "Major disruption",
  planned: "Planned work",
  change: "Service change",
};

const CATEGORY_TONE: Record<Category, string> = {
  delays: "#ff5a5a",
  planned: "#f3b247",
  change: "#57b9f4",
};

const CATEGORY_ICON: Record<Category, typeof AlertOctagon> = {
  delays: AlertOctagon,
  planned: MinusCircle,
  change: AlertOctagon,
};

function buildSubtitle(routes: string[], category: Category) {
  if (routes.length === 0) {
    return CATEGORY_TITLE[category];
  }
  const list = routes.slice(0, 2).join("/");
  return `${CATEGORY_TITLE[category]} · ${list} trains`;
}

export function LiveIncidentsList({
  alerts,
  maxItems = 4,
  nearestStopName,
}: Props) {
  const items = alerts.slice(0, maxItems);
  const activeCount = alerts.length;
  const emptyLocation = nearestStopName || "the nearest stop";

  return (
    <section className="sr-incidents-list">
      <div className="sr-incidents-list__header">
        <Eyebrow>Live Incidents</Eyebrow>
        <span
          className="sr-incidents-list__pill"
          data-empty={activeCount === 0 ? "true" : "false"}
        >
          {activeCount} ACTIVE
        </span>
      </div>

      <div className="sr-incidents-list__rows">
        {items.length === 0 ? (
          <div className="sr-incidents-list__empty">
            All clear near {emptyLocation}. No incidents flagged by MTA or ATLAS
            Intel in the last 30 min.
          </div>
        ) : (
          items.map((alert, index) => {
            const category = categoryForAlert(alert);
            const Icon = CATEGORY_ICON[category];
            const tone = CATEGORY_TONE[category];
            const routes = normalizeRoutes(alert);
            const title = buildSubtitle(routes, category);
            const detail = alertDetail(alert);

            return (
              <article
                key={alert.alert_id || `${alert.header}-${index}`}
                className="sr-incidents-list__row"
                data-category={category}
                style={{ "--sr-incident-tone": tone } as CSSProperties}
              >
                <span className="sr-incidents-list__icon" aria-hidden="true">
                  <Icon size={16} strokeWidth={1.7} />
                </span>

                <div className="sr-incidents-list__copy">
                  <div className="sr-incidents-list__title-row">
                    <span className="sr-incidents-list__title">{title}</span>
                    <time className="sr-incidents-list__time">
                      {relativeAlertTime(alert)}
                    </time>
                  </div>

                  <div className="sr-incidents-list__badges">
                    {routes.slice(0, 3).map((route) => (
                      <TrainBullet key={route} line={route} size={14} />
                    ))}
                    <span className="sr-incidents-list__source">MTA</span>
                  </div>

                  <p className="sr-incidents-list__detail">{detail}</p>
                </div>
              </article>
            );
          })
        )}
      </div>
    </section>
  );
}
