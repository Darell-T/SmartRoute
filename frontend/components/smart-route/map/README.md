# SmartRoute Map

This folder contains the route-preview map shell and map marker DOM helpers.
Subway network rendering artifacts are generated elsewhere; do not edit
generated transit artifacts from this folder.

## Current Location Marker

`route-preview-markers.ts` owns the current-location marker contract:

- `createCurrentLocationDot()` returns the Apple Maps-style blue dot DOM marker.
- `updateCurrentLocationDot()` receives browser geolocation accuracy in meters
  and current map zoom.
- `metersPerPixelAtLatitude()` converts the real GPS radius into screen pixels
  so the accuracy disc grows when the user zooms in and hides when it is too
  small to read.

`smart-route-map.tsx` must pass `position.coords.accuracy` through to
`updateCurrentLocationDot`. The marker intentionally avoids `contain: paint`
because the accuracy disc can extend beyond the 44px marker box.

The accuracy disc is not decorative. It represents the browser geolocation
uncertainty radius. Keep the conversion tied to map zoom and latitude rather
than a fixed CSS size; otherwise the disc will lie about the user's possible
location as the camera changes.

## Marker Style

The current-location marker uses a white ring, iOS-blue fill, reduced-motion
fallback, and a soft accuracy area. Destination pins stay separate and use the
route endpoint marker styling in `createDestinationPin()`.

The current-location dot should stay a DOM marker, not a generated map artifact.
That keeps the pulse, reduced-motion behavior, and accuracy sizing independent
from the transit artifact pipeline.
