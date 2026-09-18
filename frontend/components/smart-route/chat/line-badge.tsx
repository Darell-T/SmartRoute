"use client";

import { TrainBullet } from "@/components/smart-route/train-bullet";

export function LineBadge({ line, size = 22 }: { line: string; size?: number }) {
  return <TrainBullet line={line} size={size} />;
}
