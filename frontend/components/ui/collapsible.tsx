"use client";

/* Vendored from shadcn/ui (registry: new-york-v4), source:
   https://github.com/shadcn-ui/ui/blob/main/apps/v4/registry/new-york-v4/ui/collapsible.tsx
   License: MIT (https://github.com/shadcn-ui/ui/blob/main/LICENSE.md)

   Local tweaks: none — thin `data-slot` wrapper around
   `@radix-ui/react-collapsible`, copied verbatim. Backs the vendored AI
   Elements `Reasoning` component (components/ai-elements/reasoning.tsx),
   used by both the left rail's route-reasoning insights and the chat tab's
   working panel. */

import * as CollapsiblePrimitive from "@radix-ui/react-collapsible";
import type { ComponentProps } from "react";

function Collapsible(props: ComponentProps<typeof CollapsiblePrimitive.Root>) {
  return <CollapsiblePrimitive.Root data-slot="collapsible" {...props} />;
}

function CollapsibleTrigger(
  props: ComponentProps<typeof CollapsiblePrimitive.CollapsibleTrigger>,
) {
  return (
    <CollapsiblePrimitive.CollapsibleTrigger
      data-slot="collapsible-trigger"
      {...props}
    />
  );
}

function CollapsibleContent(
  props: ComponentProps<typeof CollapsiblePrimitive.CollapsibleContent>,
) {
  return (
    <CollapsiblePrimitive.CollapsibleContent
      data-slot="collapsible-content"
      {...props}
    />
  );
}

export { Collapsible, CollapsibleTrigger, CollapsibleContent };
