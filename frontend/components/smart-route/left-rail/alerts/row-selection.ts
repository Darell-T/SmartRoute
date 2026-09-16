import { useState } from "react";

export function toggleAlertRowSelection(open: boolean): boolean {
  return !open;
}

export function useAlertRowSelection(initialOpen = false): {
  open: boolean;
  toggle: () => void;
} {
  const [open, setOpen] = useState(initialOpen);

  return {
    open,
    toggle: () => setOpen((current) => toggleAlertRowSelection(current)),
  };
}
