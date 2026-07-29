"use client";

import type { RouteCard as RouteCardData } from "@/lib/agent-chat-stream";
import { RecommendedItineraryFromCards } from "./recommended-itinerary-card";
import { recommendedCardsForChat } from "./recommended-card-selection";

export { recommendedCardsForChat } from "./recommended-card-selection";

export function ChatRouteCardList({
  cards,
  selectedCardId,
  onSelect,
}: {
  cards: RouteCardData[];
  selectedCardId?: string | null;
  onSelect?: (card: RouteCardData) => void;
}) {
  const recommended = recommendedCardsForChat(cards);
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
