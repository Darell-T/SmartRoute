import { useState } from "react";

export function toggleAlertRowSelection(open: boolean): boolean {
  return !open;
}

type AlertRowSelection = {
  open: boolean;
  toggle: () => void;
};

export function useAlertRowSelection(initialOpen = false): AlertRowSelection {
  const [open, setOpen] = useState(initialOpen);

  return {
    open,
    toggle: () => setOpen((current) => toggleAlertRowSelection(current)),
  };
}
