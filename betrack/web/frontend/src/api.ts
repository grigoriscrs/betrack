export interface Sport {
  key: string;
  label: string;
  live_count: number;
}

export interface BookCounts {
  events: number;
  markets: number;
  quotes_observed: number;
  quotes_changed: number;
}

export interface Status {
  last_run: string | null;
  poll_interval: number;
  bookmakers: string[];
  counts: Record<string, BookCounts>;
  total_observed: number;
  total_changed: number;
  errors: string[];
  detection: string;
  book_last_observed?: Record<string, string>;
}

export interface Quote {
  odds: number;
  age_seconds: number | null;
  outcome_id: string;
}

export interface Outcome {
  outcome_type: string;
  label: string;
  line: number | null;
  quotes: Record<string, Quote>;
  best: string | null;
  gap_pct: number | null;
}

export interface Market {
  market_type: string;
  market_label: string;
  period: string;
  line: number | null;
  outcomes: Outcome[];
}

export interface EventRow {
  event_id: string;
  home_team: string;
  away_team: string;
  competition: string;
  country: string | null;
  status: string;
  start_time: string;
  sportradar_match_id: number | null;
  books: string[];
  headline: Market | null;
}

export interface EventDetail {
  found: boolean;
  event_id?: string;
  home_team?: string;
  away_team?: string;
  competition?: string;
  status?: string;
  start_time?: string;
  sport?: string;
  sportradar_match_id?: number | null;
  books?: string[];
  markets?: Market[];
}

export interface QuotePoint {
  decimal_odds: number;
  observed_at: string;
  source_timestamp: string | null;
}

export interface ValueOpportunity {
  type: "value";
  event_id: string;
  home_team: string;
  away_team: string;
  sport: string;
  competition: string;
  country: string | null;
  market_type: string;
  market_label: string;
  period: string;
  line: number | null;
  outcome_type: string;
  outcome_label: string;
  bookmaker: string;
  bookmaker_odds: number;
  implied_prob_at_book: number;
  fair_prob: number;
  edge_pct: number;
  betfair_back: number | null;
  betfair_lay: number | null;
  liquidity_total_matched: number | null;
  age_seconds: number | null;
  outcome_id: string;
}

export interface ArbLeg {
  outcome_type: string;
  outcome_label: string;
  line: number | null;
  bookmaker: string;
  odds: number;
  outcome_id: string;
  age_seconds: number | null;
}

export interface ArbOpportunity {
  type: "arb";
  event_id: string;
  home_team: string;
  away_team: string;
  sport: string;
  competition: string;
  country: string | null;
  market_type: string;
  market_label: string;
  period: string;
  line: number | null;
  roi_pct: number;
  legs: ArbLeg[];
}

export interface LocalDiffBook {
  odds: number;
  age_seconds: number | null;
  outcome_id: string;
}

export interface LocalDiff {
  type: "diff";
  event_id: string;
  home_team: string;
  away_team: string;
  sport: string;
  competition: string;
  country: string | null;
  market_type: string;
  market_label: string;
  period: string;
  line: number | null;
  outcome_type: string;
  outcome_label: string;
  // Per-local-book quotes that participated in this disagreement. Keyed by
  // bookmaker name; only books that actually quoted this outcome are
  // present, so a row may have 2 or 3 entries depending on coverage.
  books: Record<string, LocalDiffBook>;
  high_book: string;
  high_odds: number;
  low_book: string;
  low_odds: number;
  gap_pct: number;
  betfair_back: number | null;
  betfair_lay: number | null;
}

export interface Opportunities {
  value: ValueOpportunity[];
  arb: ArbOpportunity[];
  diffs: LocalDiff[];
}

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  status: () => getJSON<Status>("/api/status"),
  sports: () => getJSON<Sport[]>("/api/sports"),
  events: (sport: string) => getJSON<EventRow[]>(`/api/events?sport=${sport}`),
  event: (id: string) => getJSON<EventDetail>(`/api/event/${id}`),
  quoteHistory: (outcomeId: string, bookmaker: string, limit = 200) =>
    getJSON<QuotePoint[]>(`/api/quote-history/${outcomeId}?bookmaker=${bookmaker}&limit=${limit}`),
  opportunities: (
    minEdge: number,
    minGap: number,
    minGapHighOdds: number,
    minRoi: number,
    sport: string | null,
    freshSeconds: number,
  ) => {
    const params = new URLSearchParams({
      min_edge: minEdge.toString(),
      min_gap: minGap.toString(),
      min_gap_high_odds: minGapHighOdds.toString(),
      min_roi: minRoi.toString(),
      fresh_seconds: freshSeconds.toString(),
    });
    if (sport) params.set("sport", sport);
    return getJSON<Opportunities>(`/api/opportunities?${params}`);
  },
};

// Short symbol for an outcome on the headline row (1 / X / 2, O / U, etc.).
export function outcomeSymbol(outcomeType: string): string {
  switch (outcomeType) {
    case "home_win":
      return "1";
    case "draw":
      return "X";
    case "away_win":
      return "2";
    case "over":
      return "O";
    case "under":
      return "U";
    default:
      return outcomeType;
  }
}
