"use client";

import type { RouteCard as RouteCardData } from "@/lib/agent-chat/stream";
import { RecommendedItineraryFromCards } from "./recommended-itinerary-card";

export function ChatRouteCardList({
  cards,
  selectedCardId,
  onSelect,
}: {
  cards: RouteCardData[];
  selectedCardId?: string | null;
  onSelect?: (card: RouteCardData) => void;
}) {
  const recommended = cards.filter((card) => card.role === "recommended");
  if (recommended.length === 0) return null;

  return (
    <div className="sr-chat-route-cards">
      <RecommendedItineraryFromCards
        cards={recommended}
        selectedCardId={selectedCardId}
        onSelect={onSelect}
        landDelayMs={0}
        primaryActionLabel="Open on map"
      />
    </div>
  );
}
