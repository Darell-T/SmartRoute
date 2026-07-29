export type LiveFeedLocation = { lat: number; lng: number };

export interface LiveFeedSocket {
  readyState: number;
  send(payload: string): void;
  close(): void;
  onopen: ((event: Event) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent<string>) => void) | null;
}

export interface LiveFeedConnectionOptions {
  fetchTicket: () => Promise<string>;
  createSocket: (ticket: string) => LiveFeedSocket;
  onStatus: (status: "connecting" | "reconnecting" | "open" | "error") => void;
  onMessage: (payload: string) => void;
  setTimer?: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>;
  clearTimer?: (timer: ReturnType<typeof setTimeout>) => void;
}

export function normalizeRouteScope(routeIds: readonly string[]): string[] {
  return [...new Set(routeIds.map((route) => route.trim().toUpperCase()).filter(Boolean))].slice(0, 12);
}

export function shouldSendLocation(previous: LiveFeedLocation | null, next: LiveFeedLocation): boolean {
  return !previous || Math.abs(previous.lat - next.lat) > 0.00045 || Math.abs(previous.lng - next.lng) > 0.00045;
}

export class LiveFeedConnection {
  private socket: LiveFeedSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private backoff = 1_000;
  private disposed = false;
  private connecting = false;
  private location: LiveFeedLocation | null = null;
  private sentLocation: LiveFeedLocation | null = null;
  private routeIds: string[] = [];

  constructor(private readonly options: LiveFeedConnectionOptions) {}

  start(): void { this.connect(); }

  updateLocation(location: LiveFeedLocation | null): void {
    this.location = location;
    this.sendLocation(false);
  }

  updateRouteIds(routeIds: readonly string[]): void {
    const next = normalizeRouteScope(routeIds);
    if (next.join("|") === this.routeIds.join("|")) return;
    this.routeIds = next;
    this.sendScope();
  }

  dispose(): void {
    this.disposed = true;
    if (this.reconnectTimer) (this.options.clearTimer ?? clearTimeout)(this.reconnectTimer);
    this.reconnectTimer = null;
    if (this.socket) {
      this.socket.onopen = null;
      this.socket.onclose = null;
      this.socket.onerror = null;
      this.socket.onmessage = null;
      this.socket.close();
      this.socket = null;
    }
  }

  private async connect(): Promise<void> {
    if (this.disposed || this.socket || this.connecting || !this.location) return;
    this.connecting = true;
    this.options.onStatus(this.backoff === 1_000 ? "connecting" : "reconnecting");
    try {
      const ticket = await this.options.fetchTicket();
      if (this.disposed || this.socket) return;
      const socket = this.options.createSocket(ticket);
      this.socket = socket;
      socket.onopen = () => {
        if (this.disposed || socket !== this.socket) return;
        this.backoff = 1_000;
        this.options.onStatus("open");
        this.sendLocation(true);
      };
      socket.onmessage = (event) => {
        if (!this.disposed && socket === this.socket) this.options.onMessage(event.data);
      };
      socket.onerror = () => {
        if (!this.disposed && socket === this.socket) this.options.onStatus("error");
      };
      socket.onclose = () => {
        if (this.disposed || socket !== this.socket) return;
        this.socket = null;
        if (!this.disposed) this.scheduleReconnect();
      };
    } catch {
      if (!this.disposed) this.scheduleReconnect();
    } finally {
      this.connecting = false;
    }
  }

  private scheduleReconnect(): void {
    if (this.disposed || this.reconnectTimer) return;
    this.options.onStatus("reconnecting");
    const delay = Math.min(this.backoff, 30_000);
    this.reconnectTimer = (this.options.setTimer ?? setTimeout)(() => {
      this.reconnectTimer = null;
      this.backoff = Math.min(this.backoff * 2, 30_000);
      void this.connect();
    }, delay);
  }

  private sendLocation(force: boolean): void {
    if (!this.location || !this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    if (!force && !shouldSendLocation(this.sentLocation, this.location)) return;
    this.socket.send(JSON.stringify({ type: "location", ...this.location, selected_route_ids: this.routeIds }));
    this.sentLocation = this.location;
  }

  private sendScope(): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify({ type: "vehicle_scope", selected_route_ids: this.routeIds }));
  }
}
