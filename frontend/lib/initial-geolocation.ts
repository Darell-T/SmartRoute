export type Coordinates = { lat: number; lng: number };

export interface GeolocationLike {
  getCurrentPosition(
    success: (position: { coords: { latitude: number; longitude: number } }) => void,
    failure: () => void,
    options: PositionOptions,
  ): void;
}

export function requestInitialLocation(
  geolocation: GeolocationLike | undefined,
  fallback: Coordinates,
  apply: (location: Coordinates) => void,
  setTimer: (callback: () => void, delay: number) => ReturnType<typeof setTimeout> = setTimeout,
  clearTimer: (timer: ReturnType<typeof setTimeout>) => void = clearTimeout,
): () => void {
  let active = true;
  const safelyApply = (location: Coordinates) => { if (active) apply(location); };
  if (!geolocation) {
    queueMicrotask(() => safelyApply(fallback));
    return () => { active = false; };
  }
  const timeout = setTimer(() => safelyApply(fallback), 8_000);
  const finish = (location: Coordinates) => { clearTimer(timeout); safelyApply(location); };
  geolocation.getCurrentPosition(
    (position) => finish({ lat: position.coords.latitude, lng: position.coords.longitude }),
    () => finish(fallback),
    { enableHighAccuracy: false, timeout: 6_000, maximumAge: 60_000 },
  );
  return () => { active = false; clearTimer(timeout); };
}
