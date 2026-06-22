"use client";

import { Cloud } from "lucide-react";

export function WeatherChip() {
  // TODO: replace this stub with the live weather hook once that contract exists.
  return (
    <span className="sr-weather-chip" aria-label="Current weather 72 degrees Fahrenheit">
      <Cloud size={13} strokeWidth={1.5} aria-hidden="true" />
      <span>{"72\u00b0F"}</span>
    </span>
  );
}
