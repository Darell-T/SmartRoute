export interface ServiceAlertPollGate {
  startedWsEpoch: number;
  currentWsEpoch: number;
  wsIsOpen: boolean;
}

export function shouldApplyServiceAlertPoll({
  startedWsEpoch,
  currentWsEpoch,
  wsIsOpen,
}: ServiceAlertPollGate): boolean {
  return !wsIsOpen && startedWsEpoch === currentWsEpoch;
}
