import { ChevronRight } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { AlertRouteBadgeGroup } from "./alert-badges";
import {
  AlertDetailPanel,
  alertSeverityLabel,
  buildAlertDetailView,
  lineAlertSubtitle,
  shortAlertTimeLabel,
} from "./alert-detail";
import { useAlertRowSelection } from "./alert-row-selection";
import { TransitText } from "./atoms";
import type { AlertLineGroupModel } from "./alert-view-model";
import type { AlertFeedItem } from "./types";

type AlertLineGroupListProps = {
  groups: AlertLineGroupModel[];
  "aria-label": string;
};

export function AlertLineGroupList({
  groups,
  "aria-label": ariaLabel,
}: AlertLineGroupListProps) {
  return (
    <div className="sr-alert-line-group-list" role="list" aria-label={ariaLabel}>
      <AnimatePresence initial={false}>
        {groups.map((group) => (
          <AlertLineGroup key={group.id} group={group} />
        ))}
      </AnimatePresence>
    </div>
  );
}

export function AlertEmptyState() {
  return (
    <div className="sr-empty-row">
      <strong>No active alerts right now.</strong>
      <small>Service updates from today will appear here.</small>
    </div>
  );
}

function AlertLineGroup({ group }: { group: AlertLineGroupModel }) {
  return (
    <motion.article
      className="sr-alert-line-group sr-station-group"
      role="listitem"
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
    >
      <header className="sr-alert-line-header sr-station-header">
        <span className="sr-station-header__title">
          <strong>{group.name}</strong>
          <span className="sr-station-header__walk">
            {group.items.length === 1 ? "1 alert" : `${group.items.length} alerts`}
          </span>
        </span>
      </header>
      <ul className="sr-alert-line-list">
        <AnimatePresence initial={false}>
          {group.items.map((item) => (
            <AlertLineRow key={item.id} item={item} />
          ))}
        </AnimatePresence>
      </ul>
    </motion.article>
  );
}

function AlertLineRow({ item }: { item: AlertFeedItem }) {
  const { open, toggle } = useAlertRowSelection();
  const reduceMotion = useReducedMotion();
  const detail = buildAlertDetailView(item);
  const subtitle = lineAlertSubtitle(item);
  const timeLabel = shortAlertTimeLabel(item.timestampLabel);
  const header = (
    <>
      <span className="sr-alert-line-row__media">
        {item.routeIds.length > 0 ? (
          <AlertRouteBadgeGroup routeIds={item.routeIds} limit={3} size={20} />
        ) : (
          <span className="sr-alert-line-row__fallback" aria-hidden="true">
            Service
          </span>
        )}
      </span>
      <span className="sr-alert-line-row__copy">
        <strong
          className="sr-alert-line-row__title"
          title={alertSeverityLabel(item.severity)}
        >
          <TransitText text={item.title} bulletSize={13} />
        </strong>
        {subtitle && (
          <small>
            <TransitText text={subtitle} bulletSize={12} mode="paragraph" />
          </small>
        )}
      </span>
      <span className="sr-alert-line-row__meta">
        {timeLabel && <time>{timeLabel}</time>}
        {detail && (
          <ChevronRight
            className="sr-alert-line-row__chevron"
            size={15}
            strokeWidth={1.8}
            aria-hidden="true"
          />
        )}
      </span>
    </>
  );

  if (!detail) {
    return (
      <li
        className="sr-alert-line-row"
        data-severity={item.severity}
        data-lifecycle={item.lifecycle}
      >
        <div className="sr-alert-line-row__summary" data-static="true">
          {header}
        </div>
      </li>
    );
  }

  return (
    <li
      className="sr-alert-line-row"
      data-severity={item.severity}
      data-lifecycle={item.lifecycle}
      data-open={open ? "true" : "false"}
    >
      <button
        type="button"
        className="sr-alert-line-row__summary"
        aria-expanded={open}
        onClick={toggle}
      >
        {header}
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="detail"
            className="sr-alert-detail-wrap sr-alert-line-detail-wrap"
            initial={reduceMotion ? false : { height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={reduceMotion ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
            style={{ overflow: "hidden" }}
          >
            <AlertDetailPanel item={item} detail={detail} />
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  );
}
