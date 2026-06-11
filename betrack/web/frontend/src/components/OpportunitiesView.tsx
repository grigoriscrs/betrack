import { useEffect, useMemo, useState } from "react";
import { api, type Opportunities, type ValueOpportunity, type ArbOpportunity, type LocalDiff } from "../api";
import { ageColor, fmtAge, fmtOdds, fmtPct } from "../format";
import { SportIcon } from "./SportIcon";

interface Props {
  onSelect: (eventId: string) => void;
}

// Wider than the 30s poll interval so the table doesn't blink to empty during
// cycle transitions: with fresh_seconds=30 and a 22s-ish cycle, there's a ~5-8s
// window each cycle where the previous data fell outside the gate before the
// next write arrived. 60s guarantees one full cycle's worth is always fresh.
const FRESH_SECONDS = 60;

type SubTab = "value" | "arb" | "diffs";
type SportFilter = "all" | "football" | "basketball" | "tennis";

export function OpportunitiesView({ onSelect }: Props) {
  const [subTab, setSubTab] = useState<SubTab>("value");
  const [sport, setSport] = useState<SportFilter>("all");
  const [minEdge, setMinEdge] = useState(0.03);
  // Diffs use two thresholds: a normal-odds floor (default 15%) and a wider
  // floor for high-odds outcomes where both books quote >= 10 (default 50%) —
  // small absolute-prob gaps look huge in ratio terms at high odds, so we
  // need a noisier floor to surface real disagreement instead of jitter.
  const [minGap, setMinGap] = useState(0.15);
  const [minGapHighOdds, setMinGapHighOdds] = useState(0.5);
  // Sub-5% arbs aren't worth executing under our 10s polling cadence — the
  // odds-movement risk between leg-one and leg-two click eats the margin.
  const [minRoi, setMinRoi] = useState(0.05);
  const [data, setData] = useState<Opportunities | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      setLoading(true);
      try {
        const d = await api.opportunities(
          minEdge,
          minGap,
          minGapHighOdds,
          minRoi,
          sport === "all" ? null : sport,
          FRESH_SECONDS,
        );
        if (!stop) setData(d);
      } catch {
        /* transient */
      } finally {
        if (!stop) setLoading(false);
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [minEdge, minGap, minGapHighOdds, minRoi, sport]);

  const subTabBtn = (key: SubTab, label: string, count: number, colorClass: string) => (
    <button
      key={key}
      onClick={() => setSubTab(key)}
      className={`px-3 py-1.5 text-xs font-semibold uppercase tracking-wider rounded-md border transition-colors ${
        subTab === key
          ? `${colorClass} border-current bg-current/10`
          : "border-slate-800 text-slate-500 hover:text-slate-300"
      }`}
    >
      {label} <span className="ml-1 opacity-60">({count})</span>
    </button>
  );

  const sportBtn = (key: SportFilter, label: string) => (
    <button
      key={key}
      onClick={() => setSport(key)}
      className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-colors ${
        sport === key
          ? "border-emerald-400 text-emerald-300 bg-emerald-400/10"
          : "border-slate-800 text-slate-500 hover:text-slate-300"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {subTabBtn("value", "Value · vs Betfair", data?.value.length ?? 0, "text-emerald-400")}
        {subTabBtn("arb", "Arbitrage · local books", data?.arb.length ?? 0, "text-amber-400")}
        {subTabBtn("diffs", "Diffs · Stoix vs Novi", data?.diffs.length ?? 0, "text-sky-400")}
        {loading && data && <span className="text-xs text-slate-600 ml-auto">refreshing…</span>}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-slate-500 mr-1">Sport:</span>
        {sportBtn("all", "All")}
        {sportBtn("football", "Football")}
        {sportBtn("basketball", "Basketball")}
        {sportBtn("tennis", "Tennis")}
      </div>

      {subTab === "value" && (
        <ValueTabBody minEdge={minEdge} setMinEdge={setMinEdge} rows={data?.value ?? []} onSelect={onSelect} />
      )}
      {subTab === "arb" && (
        <ArbTabBody minRoi={minRoi} setMinRoi={setMinRoi} rows={data?.arb ?? []} onSelect={onSelect} />
      )}
      {subTab === "diffs" && (
        <DiffsTabBody
          minGap={minGap}
          setMinGap={setMinGap}
          minGapHighOdds={minGapHighOdds}
          setMinGapHighOdds={setMinGapHighOdds}
          rows={data?.diffs ?? []}
          onSelect={onSelect}
        />
      )}
    </div>
  );
}

function Slider({
  label,
  min,
  max,
  step,
  value,
  onChange,
  help,
}: {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (v: number) => void;
  help: string;
}) {
  return (
    <div className="flex items-center gap-3 text-sm text-slate-400">
      <span>{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-56 accent-emerald-500"
      />
      <span className="text-white font-semibold w-12 tabular-nums">{(value * 100).toFixed(1)}%</span>
      <span className="text-xs text-slate-600">{help}</span>
    </div>
  );
}

function ValueTabBody({
  minEdge,
  setMinEdge,
  rows,
  onSelect,
}: {
  minEdge: number;
  setMinEdge: (v: number) => void;
  rows: ValueOpportunity[];
  onSelect: (id: string) => void;
}) {
  return (
    <>
      <Slider
        label="Min edge"
        min={0.005}
        max={0.10}
        step={0.005}
        value={minEdge}
        onChange={setMinEdge}
        help={`freshness ≤ ${FRESH_SECONDS}s · fair value from Betfair midpoint (sharp reference)`}
      />
      {rows.length === 0 ? (
        <Empty msg="No value at this threshold. Lower the slider or wait for the next cycle." />
      ) : (
        <ValueTable rows={rows} onSelect={onSelect} />
      )}
    </>
  );
}

function ArbTabBody({
  minRoi,
  setMinRoi,
  rows,
  onSelect,
}: {
  minRoi: number;
  setMinRoi: (v: number) => void;
  rows: ArbOpportunity[];
  onSelect: (id: string) => void;
}) {
  return (
    <>
      <Slider
        label="Min ROI"
        min={0.005}
        max={0.20}
        step={0.005}
        value={minRoi}
        onChange={setMinRoi}
        help="riskless return on a fully-funded arb — sub-5% is fragile at our 10s polling cadence"
      />
      <p className="text-xs text-slate-500">
        Cross-book arbitrage across the local books (Stoiximan, Novibet, Pamestoixima). Betfair is
        reference-only and never a bet target. Markets where any two local books disagree by &gt; 2×
        implied probability on a leg are filtered as game-state mismatches.
      </p>
      {rows.length === 0 ? <Empty msg="No clean arbs at this threshold." /> : <ArbTable rows={rows} onSelect={onSelect} />}
    </>
  );
}

function DiffsTabBody({
  minGap,
  setMinGap,
  minGapHighOdds,
  setMinGapHighOdds,
  rows,
  onSelect,
}: {
  minGap: number;
  setMinGap: (v: number) => void;
  minGapHighOdds: number;
  setMinGapHighOdds: (v: number) => void;
  rows: LocalDiff[];
  onSelect: (id: string) => void;
}) {
  return (
    <>
      <Slider
        label="Min gap"
        min={0.005}
        max={0.50}
        step={0.005}
        value={minGap}
        onChange={setMinGap}
        help="ratio gap between the highest and lowest local-book quote on the same outcome — used when at least one of the high/low pair quotes < 10.0"
      />
      <Slider
        label="Min gap (odds ≥ 10)"
        min={0.10}
        max={2.0}
        step={0.05}
        value={minGapHighOdds}
        onChange={setMinGapHighOdds}
        help="wider floor for high-odds outcomes — at 10+ a 50% ratio gap reflects only a few absolute prob points"
      />
      {rows.length === 0 ? (
        <Empty msg="No local-book diffs at this threshold." />
      ) : (
        <DiffsTable rows={rows} onSelect={onSelect} />
      )}
    </>
  );
}

function Empty({ msg }: { msg: string }) {
  return <div className="text-center text-slate-500 py-12 text-sm">{msg}</div>;
}

// "Country - League" formatter shared by Opportunities + Events rows. Falls
// back gracefully: missing country → just the league; missing both → the
// sport name in caps so the slot never collapses to empty.
export function eventSubtitle(sport: string, competition: string, country?: string | null): string {
  const c = (country || "").trim();
  const l = (competition || "").trim();
  if (c && l) return `${c} - ${l}`;
  if (c) return c;
  if (l) return l;
  return (sport || "").toUpperCase();
}

// Shared row-leading cell across all three opportunity tables: a sport icon on
// the left, the match title above the country-and-league subtitle.
function EventCell({
  sport,
  home_team,
  away_team,
  competition,
  country,
}: {
  sport: string;
  home_team: string;
  away_team: string;
  competition: string;
  country: string | null;
}) {
  return (
    <td className="py-2 pr-3">
      <div className="flex items-center gap-3">
        <SportIcon sport={sport} className="w-5 h-5 flex-shrink-0" />
        <div className="min-w-0">
          <div className="text-white text-sm truncate">
            {home_team} <span className="text-slate-600">v</span> {away_team}
          </div>
          <div className="text-xs text-slate-500 truncate">
            {eventSubtitle(sport, competition, country)}
          </div>
        </div>
      </div>
    </td>
  );
}

function ValueTable({ rows, onSelect }: { rows: ValueOpportunity[]; onSelect: (id: string) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-slate-500 text-xs uppercase tracking-wider border-b border-slate-800">
            <th className="py-2 pr-3 text-left font-medium">Event</th>
            <th className="py-2 px-2 text-left font-medium">Market</th>
            <th className="py-2 px-2 text-left font-medium">Outcome</th>
            <th className="py-2 px-2 text-right font-medium">Book</th>
            <th className="py-2 px-2 text-right font-medium">Odds</th>
            <th className="py-2 px-2 text-right font-medium">Fair</th>
            <th className="py-2 px-2 text-right font-medium">Edge</th>
            <th className="py-2 px-2 text-right font-medium">Matched £</th>
            <th className="py-2 pl-2 text-right font-medium">Age</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((v, i) => (
            <tr
              key={`v-${i}-${v.outcome_id}`}
              onClick={() => onSelect(v.event_id)}
              className="border-b border-slate-900 hover:bg-slate-900/60 cursor-pointer"
            >
              <EventCell sport={v.sport} home_team={v.home_team} away_team={v.away_team} competition={v.competition} country={v.country} />
              <td className="py-2 px-2 text-slate-300">{v.market_label}</td>
              <td className="py-2 px-2 text-slate-300">{v.outcome_label}</td>
              <td className="py-2 px-2 text-right text-slate-300">{v.bookmaker}</td>
              <td className="py-2 px-2 text-right text-white font-semibold tabular-nums">
                {fmtOdds(v.bookmaker_odds)}
              </td>
              <td className="py-2 px-2 text-right text-slate-400 tabular-nums">{fmtOdds(1 / v.fair_prob)}</td>
              <td className="py-2 px-2 text-right text-emerald-300 font-semibold tabular-nums">
                {fmtPct(v.edge_pct)}
              </td>
              <td className="py-2 px-2 text-right text-slate-500 tabular-nums">
                {v.liquidity_total_matched != null
                  ? Math.round(v.liquidity_total_matched).toLocaleString()
                  : "—"}
              </td>
              <td className={`py-2 pl-2 text-right text-xs tabular-nums ${ageColor(v.age_seconds)}`}>
                {fmtAge(v.age_seconds)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ArbTable({ rows, onSelect }: { rows: ArbOpportunity[]; onSelect: (id: string) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-slate-500 text-xs uppercase tracking-wider border-b border-slate-800">
            <th className="py-2 pr-3 text-left font-medium">Event</th>
            <th className="py-2 px-2 text-left font-medium">Market</th>
            <th className="py-2 px-2 text-right font-medium">ROI</th>
            <th className="py-2 px-2 text-left font-medium">Legs</th>
            <th className="py-2 pl-2 text-right font-medium">Age</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((a, i) => {
            const maxAge = Math.max(...a.legs.map((l) => l.age_seconds ?? 0));
            return (
              <tr
                key={`a-${i}-${a.event_id}-${a.market_type}-${a.line}`}
                onClick={() => onSelect(a.event_id)}
                className="border-b border-slate-900 hover:bg-slate-900/60 cursor-pointer"
              >
                <EventCell sport={a.sport} home_team={a.home_team} away_team={a.away_team} competition={a.competition} country={a.country} />
                <td className="py-2 px-2 text-slate-300">{a.market_label}</td>
                <td className="py-2 px-2 text-right text-amber-300 font-semibold tabular-nums">
                  {fmtPct(a.roi_pct)}
                </td>
                <td className="py-2 px-2">
                  <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
                    {a.legs.map((leg, li) => (
                      <span key={li} className="text-slate-300">
                        <span className="text-slate-500">{leg.outcome_label}</span>{" "}
                        <span className="text-slate-400">{leg.bookmaker}</span>{" "}
                        <span className="text-white font-semibold tabular-nums">{fmtOdds(leg.odds)}</span>
                      </span>
                    ))}
                  </div>
                </td>
                <td className={`py-2 pl-2 text-right text-xs tabular-nums ${ageColor(maxAge)}`}>
                  {fmtAge(maxAge)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Canonical column order — known local books first in a stable reading
// order; any unknown books fall to the end, alphabetised. Lets the same
// table layout extend to a 4th local book without code changes.
const LOCAL_BOOK_ORDER = ["Stoiximan", "Novibet", "Pamestoixima"];
function sortBooks(books: string[]): string[] {
  return [...books].sort((a, b) => {
    const ia = LOCAL_BOOK_ORDER.indexOf(a);
    const ib = LOCAL_BOOK_ORDER.indexOf(b);
    if (ia === -1 && ib === -1) return a.localeCompare(b);
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });
}

function DiffsTable({ rows, onSelect }: { rows: LocalDiff[]; onSelect: (id: string) => void }) {
  // Column set is the union of books that appear in any row, so the table
  // adapts to whatever local books are configured backend-side.
  const localBooks = useMemo(() => {
    const seen = new Set<string>();
    for (const r of rows) for (const bm of Object.keys(r.books)) seen.add(bm);
    return sortBooks([...seen]);
  }, [rows]);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-slate-500 text-xs uppercase tracking-wider border-b border-slate-800">
            <th className="py-2 pr-3 text-left font-medium">Event</th>
            <th className="py-2 px-2 text-left font-medium">Market</th>
            <th className="py-2 px-2 text-left font-medium">Outcome</th>
            {localBooks.map((bm) => (
              <th key={bm} className="py-2 px-2 text-right font-medium">{bm}</th>
            ))}
            <th className="py-2 px-2 text-right font-medium">Gap</th>
            <th className="py-2 px-2 text-right font-medium" title="Betfair midpoint reference, not used in gating">
              Betfair (ref)
            </th>
            <th className="py-2 pl-2 text-right font-medium">Age</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d, i) => {
            const maxAge = Math.max(
              ...Object.values(d.books).map((b) => b.age_seconds ?? 0),
              0,
            );
            const bf =
              d.betfair_back != null && d.betfair_lay != null
                ? `${fmtOdds(d.betfair_back)} / ${fmtOdds(d.betfair_lay)}`
                : d.betfair_back != null
                ? fmtOdds(d.betfair_back)
                : "—";
            return (
              <tr
                key={`d-${i}-${d.high_book}-${d.books[d.high_book]?.outcome_id ?? ""}`}
                onClick={() => onSelect(d.event_id)}
                className="border-b border-slate-900 hover:bg-slate-900/60 cursor-pointer"
              >
                <EventCell sport={d.sport} home_team={d.home_team} away_team={d.away_team} competition={d.competition} country={d.country} />
                <td className="py-2 px-2 text-slate-300">{d.market_label}</td>
                <td className="py-2 px-2 text-slate-300">{d.outcome_label}</td>
                {localBooks.map((bm) => {
                  const q = d.books[bm];
                  if (!q) {
                    return <td key={bm} className="py-2 px-2 text-right text-slate-700 tabular-nums">—</td>;
                  }
                  const isHigh = bm === d.high_book;
                  const isLow = bm === d.low_book;
                  return (
                    <td
                      key={bm}
                      className={`py-2 px-2 text-right tabular-nums ${
                        isHigh ? "text-emerald-300 font-semibold"
                          : isLow ? "text-slate-500"
                          : "text-slate-400"
                      }`}
                    >
                      {fmtOdds(q.odds)}
                    </td>
                  );
                })}
                <td className="py-2 px-2 text-right text-sky-300 font-semibold tabular-nums">{fmtPct(d.gap_pct)}</td>
                <td className="py-2 px-2 text-right text-slate-500 tabular-nums text-xs">{bf}</td>
                <td className={`py-2 pl-2 text-right text-xs tabular-nums ${ageColor(maxAge)}`}>{fmtAge(maxAge)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
