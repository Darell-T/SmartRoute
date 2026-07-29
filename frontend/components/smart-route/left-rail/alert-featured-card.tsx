import { AlertRouteBadge, AlertRouteBadgeGroup } from "./alert-badges";
import {
  featuredAlertAdvice,
  featuredAlertBody,
  shortAlertTimeLabel,
} from "./alert-detail";
import { isSystemwideAlert } from "./alert-view-model";
import { TransitText } from "./atoms";
import type { AlertFeedItem } from "./types";

type AlertCardProps = {
  item: AlertFeedItem;
};

export function AlertCard({ item }: AlertCardProps) {
  const bodyText = featuredAlertBody(item);
  const adviceText = featuredAlertAdvice(item, bodyText);
  const updated = shortAlertTimeLabel(item.timestampLabel);

  return (
    <li className="sr-alert-card" data-lifecycle={item.lifecycle}>
      <div className="sr-alert-card__header" data-static="true">
        <span className="sr-alert-card__toprow">
          <span className="sr-alert-card__identity">
            {isSystemwideAlert(item) ? (
              <span className="sr-alert-systemwide">Systemwide</span>
            ) : item.routeIds.length > 2 ? (
              <AlertRouteBadge routeId={item.routeIds[0]} size={26} />
            ) : (
              <AlertRouteBadgeGroup routeIds={item.routeIds} limit={2} size={26} />
            )}
            <span className="sr-alert-card__service">{item.serviceName}</span>
          </span>
          {item.statusLabel && (
            <span className="sr-alert-status">{item.statusLabel}</span>
          )}
        </span>
        <span className="sr-alert-card__headline sr-alert-card__headline--full">
          <TransitText text={item.title} bulletSize={17} />
        </span>
        {bodyText && (
          <span className="sr-alert-card__summary sr-alert-card__summary--full">
            <TransitText text={bodyText} bulletSize={13} mode="paragraph" />
          </span>
        )}
        {adviceText && (
          <span className="sr-alert-card__advice">
            <TransitText text={adviceText} bulletSize={12} mode="paragraph" />
          </span>
        )}
        {updated && (
          <span className="sr-alert-card__footer">
            <time>Updated {updated}</time>
          </span>
        )}
      </div>
    </li>
  );
}
