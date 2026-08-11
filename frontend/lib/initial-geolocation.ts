export type Coordinates = { lat: number; lng: number };

export type InitialLocationState =
  | { status: "pending" }
  | { status: "precise_nyc"; coordinates: Coordinates }
  | { status: "fallback_nyc"; coordinates: Coordinates }
  | { status: "outside_service_area" };

type ResolvedInitialLocationState = Exclude<InitialLocationState, { status: "pending" }>;

const NYC_SERVICE_AREA = {
  minLat: 40.4774,
  maxLat: 40.9176,
  minLng: -74.2591,
  maxLng: -73.7004,
} as const;

export interface GeolocationLike {
  getCurrentPosition(
    success: (position: { coords: { latitude: number; longitude: number } }) => void,
    failure: () => void,
    options: PositionOptions,
  ): void;
}

export function locationStateForCoordinates(
  coordinates: Coordinates,
  source: "precise" | "fallback" = "precise",
): ResolvedInitialLocationState {
  const isInNyc =
    coordinates.lat >= NYC_SERVICE_AREA.minLat &&
    coordinates.lat <= NYC_SERVICE_AREA.maxLat &&
    coordinates.lng >= NYC_SERVICE_AREA.minLng &&
    coordinates.lng <= NYC_SERVICE_AREA.maxLng;

  if (!isInNyc) return { status: "outside_service_area" };
  return source === "fallback"
    ? { status: "fallback_nyc", coordinates }
    : { status: "precise_nyc", coordinates };
}

export function nextLocationState(
  current: InitialLocationState,
  update: ResolvedInitialLocationState,
): InitialLocationState {
  if (
    update.status === "fallback_nyc" &&
    (current.status === "precise_nyc" || current.status === "outside_service_area")
  ) {
    return current;
  }
  return update;
}

export function requestInitialLocation(
  geolocation: GeolocationLike | undefined,
  fallback: Coordinates,
  apply: (location: ResolvedInitialLocationState) => void,
  setTimer: (callback: () => void, delay: number) => ReturnType<typeof setTimeout> = setTimeout,
  clearTimer: (timer: ReturnType<typeof setTimeout>) => void = clearTimeout,
): () => void {
  let active = true;
  let settled = false;
  const safelyApply = (location: ResolvedInitialLocationState) => {
    if (!active || settled) return;
    settled = true;
    apply(location);
  };
  if (!geolocation) {
    queueMicrotask(() =>
      safelyApply(locationStateForCoordinates(fallback, "fallback")),
    );
    return () => { active = false; };
  }
  const timeout = setTimer(
    () => safelyApply(locationStateForCoordinates(fallback, "fallback")),
    8_000,
  );
  const finish = (location: ResolvedInitialLocationState) => {
    clearTimer(timeout);
    safelyApply(location);
  };
  geolocation.getCurrentPosition(
    (position) =>
      finish(
        locationStateForCoordinates({
          lat: position.coords.latitude,
          lng: position.coords.longitude,
        }),
      ),
    () => finish(locationStateForCoordinates(fallback, "fallback")),
    { enableHighAccuracy: false, timeout: 6_000, maximumAge: 60_000 },
  );
  return () => { active = false; clearTimer(timeout); };
}
