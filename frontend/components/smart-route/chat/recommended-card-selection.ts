import type { RouteCard } from "@/lib/agent-chat-stream";

/**
 * Chat presents one recommendation. Alternatives stay on the source turn for
 * the map workspace, so this filter must remain non-mutating.
 */
export function recommendedCardsForChat(cards: RouteCard[]): RouteCard[] {
  return cards.filter((card) => card.role === "recommended");
}
