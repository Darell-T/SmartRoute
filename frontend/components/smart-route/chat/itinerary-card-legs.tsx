import type { CSSProperties } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Bus, Clock, NavArrowDown, Train } from "iconoir-react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faArrowRightArrowLeft,
  faLocationDot,
} from "@fortawesome/free-solid-svg-icons";
import { getRouteColor } from "@/lib/mta-colors";
import {
  SUBWAY_BULLET_ROUTES,
  TrainBullet,
} from "@/components/smart-route/train-bullet";
import { warnUnsupportedRouteId, type ItineraryEvent } from "./itinerary-view-model";
import { WalkingIcon } from "./walking-icon";

export const LAYOUT_EASE = [0.22, 1, 0.36, 1] as const;

export function JourneyTitle({ names, id }: { names: string[]; id: string }) {
  return (
    <h2 id={id} className="sr-itinerary-card__title" aria-label={names.join(" to ")}>
      {names.map((name, index) => (
        <span
          key={`${name}-${index}`}
          className="sr-itinerary-card__title-part"
          aria-hidden="true"
        >
          {index > 0 ? (
            <span className="sr-itinerary-card__title-arrow">→</span>
          ) : null}
          <span>{name}</span>
        </span>
      ))}
    </h2>
  );
}

function RouteGlyph({
  routeId,
  kind,
}: {
  routeId: string;
  kind: ItineraryEvent["kind"];
}) {
  const normalized = routeId.trim().toUpperCase();
  if (!normalized) return null;
  if (kind === "bus") {
    return (
      <span className="sr-itinerary-card__bus-glyph" aria-hidden="true">
        <Bus width={17} height={17} strokeWidth={1.8} fill="currentColor" />
      </span>
    );
  }
  if (kind === "rail") {
    return (
      <span className="sr-itinerary-card__rail-glyph" aria-hidden="true">
        <Train width={17} height={17} strokeWidth={1.8} />
      </span>
    );
  }
  if (!SUBWAY_BULLET_ROUTES.has(normalized)) {
    const looksLikeBus = /[A-Z]{1,3}\d/.test(normalized) || normalized.length > 2;
    if (!looksLikeBus) warnUnsupportedRouteId(normalized);
  }
  return <TrainBullet line={normalized} size={24} />;
}

function intermediateStops(event: ItineraryEvent): string[] {
  const stops = [...(event.stops ?? [])];
  if (event.fromLabel && stops[0] === event.fromLabel) stops.shift();
  if (event.toLabel && stops.at(-1) === event.toLabel) stops.pop();
  return stops;
}

function StopChain({
  event,
  expanded,
  rideLabel,
  canExpand,
  onToggle,
  reduceMotion,
}: {
  event: ItineraryEvent;
  expanded: boolean;
  rideLabel: string;
  canExpand: boolean;
  onToggle: () => void;
  reduceMotion: boolean;
}) {
  const stops = intermediateStops(event);
  const disclosure = rideLabel ? (
    canExpand ? (
      <button
        type="button"
        className="sr-itinerary-card__disclosure"
        aria-expanded={expanded}
        aria-controls={`${event.id}-stops`}
        onClick={onToggle}
      >
        <span className="sr-itinerary-card__chain-summary-marker" aria-hidden="true" />
        <span>Ride {rideLabel}</span>
        <motion.span
          className="sr-itinerary-card__disclosure-icon"
          animate={{ rotate: expanded ? 180 : 0 }}
          transition={
            reduceMotion ? { duration: 0 } : { duration: 0.3, ease: LAYOUT_EASE }
          }
        >
          <NavArrowDown width={14} height={14} strokeWidth={1.8} />
        </motion.span>
      </button>
    ) : (
      <p className="sr-itinerary-card__ride-summary">
        <span className="sr-itinerary-card__chain-summary-marker" aria-hidden="true" />
        <span>Ride {rideLabel}</span>
      </p>
    )
  ) : null;

  return (
    <div
      className="sr-itinerary-card__stop-chain"
      data-expanded={expanded ? "true" : "false"}
      style={
        {
          "--sr-route-color":
            event.kind === "bus" ? "#5f8fd9" : getRouteColor(event.routeIds[0] ?? ""),
        } as CSSProperties
      }
    >
      <div className="sr-itinerary-card__chain-track">
        <div className="sr-itinerary-card__chain-row">
          <span className="sr-itinerary-card__chain-marker sr-itinerary-card__chain-marker--start" />
          <span className="sr-itinerary-card__station">{event.fromLabel ?? "Board"}</span>
        </div>
        {disclosure}
        <AnimatePresence initial={false}>
          {expanded && stops.length > 0 ? (
            <motion.ol
              id={`${event.id}-stops`}
              className="sr-itinerary-card__intermediate-stops"
              initial={reduceMotion ? false : { opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={reduceMotion ? undefined : { opacity: 0, height: 0 }}
              transition={
                reduceMotion ? { duration: 0 } : { duration: 0.3, ease: LAYOUT_EASE }
              }
            >
              {stops.map((stop, index) => (
                <motion.li
                  key={`${event.id}-${stop}-${index}`}
                  initial={reduceMotion ? false : { opacity: 0, y: -2 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={
                    reduceMotion
                      ? { duration: 0 }
                      : { duration: 0.16, delay: index * 0.018 }
                  }
                >
                  <span className="sr-itinerary-card__chain-marker sr-itinerary-card__chain-marker--mid" />
                  <span>{stop}</span>
                </motion.li>
              ))}
            </motion.ol>
          ) : null}
        </AnimatePresence>
        <div className="sr-itinerary-card__chain-row">
          <span className="sr-itinerary-card__chain-marker sr-itinerary-card__chain-marker--end" />
          <span className="sr-itinerary-card__station">{event.toLabel ?? event.title}</span>
        </div>
      </div>
    </div>
  );
}

function TransitLeg({
  event,
  expanded,
  onToggle,
  reduceMotion,
}: {
  event: ItineraryEvent;
  expanded: boolean;
  onToggle: () => void;
  reduceMotion: boolean;
}) {
  const stopNames = intermediateStops(event);
  const stopsLabel =
    typeof event.stopCount === "number"
      ? `${event.stopCount} ${event.stopCount === 1 ? "stop" : "stops"}`
      : null;
  const rideLabel = [stopsLabel, event.durationLabel].filter(Boolean).join(", ");
  return (
    <section className="sr-itinerary-card__leg" aria-label={`${event.kind} leg`}>
      <div className="sr-itinerary-card__leg-glyph">
        <RouteGlyph routeId={event.routeIds[0] ?? ""} kind={event.kind} />
      </div>
      <div className="sr-itinerary-card__leg-body">
        <div className="sr-itinerary-card__leg-heading">
          {event.kind === "bus" && event.routeIds[0] ? (
            <span className="sr-itinerary-card__bus-route">{event.routeIds[0].toUpperCase()}</span>
          ) : null}
          <span>{event.fromLabel ?? "Board"}</span>
          <span className="sr-itinerary-card__leg-arrow" aria-hidden="true">→</span>
          <span>{event.toLabel ?? event.title}</span>
        </div>
        <StopChain
          event={event}
          expanded={expanded}
          rideLabel={rideLabel}
          canExpand={stopNames.length > 0}
          onToggle={onToggle}
          reduceMotion={reduceMotion}
        />
      </div>
    </section>
  );
}

function WalkingLeg({ event }: { event: ItineraryEvent }) {
  return (
    <section className="sr-itinerary-card__walk" aria-label="Walking directions">
      <span className="sr-itinerary-card__walk-icon" aria-hidden="true">
        <WalkingIcon />
      </span>
      <div className="sr-itinerary-card__walk-copy">
        <div className="sr-itinerary-card__walk-heading">
          <span>{event.fromLabel ?? "Walk"}</span>
          <span className="sr-itinerary-card__leg-arrow" aria-hidden="true">→</span>
          <span>{event.toLabel ?? event.title}</span>
        </div>
      </div>
      {event.durationLabel ? (
        <span className="sr-itinerary-card__walk-duration">
          {event.durationLabel}
        </span>
      ) : null}
    </section>
  );
}

function TransferLeg({ event }: { event: ItineraryEvent }) {
  return (
    <section className="sr-itinerary-card__leg sr-itinerary-card__transfer" aria-label="Transit transfer">
      <span className="sr-itinerary-card__leg-glyph" aria-hidden="true">
        <FontAwesomeIcon icon={faArrowRightArrowLeft} />
      </span>
      <div className="sr-itinerary-card__leg-body">
        <div className="sr-itinerary-card__leg-heading"><span>{event.title}</span></div>
        {event.subtitle ? <p className="sr-itinerary-card__walk-meta">{event.subtitle}</p> : null}
      </div>
    </section>
  );
}

function WaitLeg({ event }: { event: ItineraryEvent }) {
  return (
    <section className="sr-itinerary-card__walk" aria-label="Wait before boarding">
      <span className="sr-itinerary-card__walk-icon" aria-hidden="true">
        <Clock width={17} height={17} strokeWidth={1.8} />
      </span>
      <div className="sr-itinerary-card__walk-copy">
        <div className="sr-itinerary-card__walk-heading">
          <span>{event.title}</span>
          {event.subtitle ? <span> at {event.subtitle}</span> : null}
        </div>
      </div>
      {event.durationLabel ? (
        <span className="sr-itinerary-card__walk-duration">
          {event.durationLabel}
        </span>
      ) : null}
    </section>
  );
}

function WaypointStop({ event }: { event: ItineraryEvent }) {
  return (
    <section className="sr-itinerary-card__waypoint" aria-label="Planned stop">
      <span className="sr-itinerary-card__waypoint-icon" aria-hidden="true">
        <FontAwesomeIcon icon={faLocationDot} />
      </span>
      <div>
        <p className="sr-itinerary-card__waypoint-title">{event.title}</p>
        <p className="sr-itinerary-card__waypoint-meta">
          {[event.subtitle, event.sourceLabel].filter(Boolean).join(" · ")}
        </p>
      </div>
    </section>
  );
}

export function ItineraryLeg({
  event,
  expanded,
  onToggle,
  reduceMotion,
}: {
  event: ItineraryEvent;
  expanded: boolean;
  onToggle: () => void;
  reduceMotion: boolean;
}) {
  if (event.kind === "subway" || event.kind === "bus" || event.kind === "rail") {
    return (
      <TransitLeg
        event={event}
        expanded={expanded}
        onToggle={onToggle}
        reduceMotion={reduceMotion}
      />
    );
  }
  if (event.kind === "walk") return <WalkingLeg event={event} />;
  if (event.kind === "wait") return <WaitLeg event={event} />;
  if (event.kind === "transfer") return <TransferLeg event={event} />;
  if (event.kind === "waypoint" || event.kind === "pickup") {
    return <WaypointStop event={event} />;
  }
  return null;
}
