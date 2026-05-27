export interface Status {
  last_run: string | null;
  live: number;
  prematch: number;
  scanned: number;
  covered: number;
  quota_remaining: string | null;
  bookmakers: string[];
  reference: string;
  poll_interval: number;
}

export interface ArbLeg {
  bookmaker: string;
  odds: number;
}

export interface Opportunity {
  id: number;
  signature: string;
  kind: "value" | "arb";
  event_id: string;
  event_label: string;
  competition: string;
  status: string;
  market_label: string;
  outcome_label: string | null;
  bookmaker: string | null;
  bookmaker_odds: number | null;
  reference_odds: number | null;
  edge_pct: number | null;
  margin: number | null;
  legs?: Record<string, ArbLeg> | null;
  first_seen: string;
  last_seen: string;
  active: boolean;
  alerted: boolean;
  duration_seconds: number;
}

export interface EventOutcome {
  outcome_label: string;
  outcome_type: string;
  quotes: Record<string, number>;
  edge_pct: number | null;
}

export interface EventMarket {
  market_label: string;
  market_type: string;
  line: number | null;
  outcomes: EventOutcome[];
}

export interface EventDetail {
  found: boolean;
  event_id?: string;
  event_label?: string;
  competition?: string;
  status?: string;
  start_time?: string;
  bookmakers?: string[];
  reference?: string;
  markets?: EventMarket[];
}

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  status: () => getJSON<Status>("/api/status"),
  opportunities: () => getJSON<Opportunity[]>("/api/opportunities"),
  history: (limit = 200) => getJSON<Opportunity[]>(`/api/history?limit=${limit}`),
  event: (id: string) => getJSON<EventDetail>(`/api/events/${id}`),
};

export function edgeValue(o: Opportunity): number | null {
  return o.kind === "arb" ? o.margin : o.edge_pct;
}
