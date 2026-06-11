import { useMemo } from "react";
import { outcomeSymbol, type EventRow } from "../api";
import { ageColor, fmtAge, fmtOdds, fmtPct } from "../format";
import { SportIcon } from "./SportIcon";
import { eventSubtitle } from "./OpportunitiesView";

interface Props {
  rows: EventRow[];
  sport: string;
  selectedBookmakers: string[];
  onSelect: (eventId: string) => void;
}

function maxAgeOnRow(ev: EventRow, books: string[]): number | null {
  let max: number | null = null;
  for (const o of ev.headline?.outcomes ?? []) {
    for (const b of books) {
      const q = o.quotes[b];
      if (q?.age_seconds != null) max = max == null ? q.age_seconds : Math.max(max, q.age_seconds);
    }
  }
  return max;
}

function bestGap(ev: EventRow): number | null {
  let max: number | null = null;
  for (const o of ev.headline?.outcomes ?? []) {
    if (o.gap_pct != null) max = max == null ? o.gap_pct : Math.max(max, o.gap_pct);
  }
  return max;
}

export function EventsTable({ rows, sport, selectedBookmakers, onSelect }: Props) {
  // Outcome columns derived from whichever row first has a headline.
  const cols = useMemo(() => {
    const first = rows.find((r) => r.headline);
    return first?.headline?.outcomes.map((o) => o.outcome_type) ?? [];
  }, [rows]);

  if (rows.length === 0) {
    return <div className="text-center text-slate-500 py-16 text-sm">No live events right now.</div>;
  }
  if (selectedBookmakers.length === 0) {
    return (
      <div className="text-center text-slate-500 py-16 text-sm">
        Pick at least one bookmaker filter to show odds.
      </div>
    );
  }
  if (cols.length === 0) {
    return (
      <div className="text-center text-slate-500 py-16 text-sm">
        No headline market yet for this sport. Wait a cycle.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-separate border-spacing-0">
        <thead>
          <tr className="text-slate-500 text-[10px] uppercase tracking-wider">
            <th rowSpan={2} className="py-2 pr-3 text-left font-medium align-bottom border-b border-slate-800">
              Match
            </th>
            {selectedBookmakers.map((b, idx) => (
              <th
                key={b}
                colSpan={cols.length}
                className={`py-1 px-2 text-center font-semibold text-slate-400 border-b border-slate-800 ${
                  idx > 0 ? "border-l border-slate-800" : ""
                }`}
              >
                {b}
              </th>
            ))}
            <th rowSpan={2} className="py-2 px-2 text-right font-medium align-bottom border-b border-slate-800 border-l border-slate-800">
              Gap
            </th>
            <th rowSpan={2} className="py-2 pl-2 text-right font-medium align-bottom border-b border-slate-800">
              Age
            </th>
          </tr>
          <tr className="text-slate-500 text-[10px] uppercase tracking-wider">
            {selectedBookmakers.map((b, bi) =>
              cols.map((c, ci) => (
                <th
                  key={`${b}-${c}`}
                  className={`py-1.5 px-2 text-center font-medium border-b border-slate-800 ${
                    ci === 0 && bi > 0 ? "border-l border-slate-800" : ""
                  }`}
                >
                  {outcomeSymbol(c)}
                </th>
              ))
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((ev) => {
            const age = maxAgeOnRow(ev, selectedBookmakers);
            const gap = bestGap(ev);
            return (
              <tr
                key={ev.event_id}
                onClick={() => onSelect(ev.event_id)}
                className="border-b border-slate-900 hover:bg-slate-900/60 cursor-pointer"
              >
                <td className="py-2 pr-3 border-b border-slate-900">
                  <div className="flex items-center gap-3">
                    <SportIcon sport={sport} className="w-5 h-5 flex-shrink-0" />
                    <div className="min-w-0">
                      <div className="text-white truncate">
                        {ev.home_team} <span className="text-slate-600">v</span> {ev.away_team}
                      </div>
                      <div className="text-xs text-slate-500 truncate">
                        {eventSubtitle(sport, ev.competition, ev.country)}
                      </div>
                    </div>
                  </div>
                </td>
                {selectedBookmakers.map((b, bi) =>
                  cols.map((c, ci) => {
                    const outcome = ev.headline?.outcomes.find((o) => o.outcome_type === c);
                    const q = outcome?.quotes[b];
                    const isBest = outcome?.best === b;
                    return (
                      <td
                        key={`${b}-${c}`}
                        className={`py-2 px-2 text-center tabular-nums border-b border-slate-900 ${
                          ci === 0 && bi > 0 ? "border-l border-slate-800" : ""
                        } ${isBest ? "text-emerald-300 font-semibold" : q ? "text-slate-300" : "text-slate-700"}`}
                        title={q ? `${b} · ${fmtAge(q.age_seconds)} ago` : `${b}: no data`}
                      >
                        {q ? fmtOdds(q.odds) : "—"}
                      </td>
                    );
                  })
                )}
                <td className="py-2 px-2 text-right border-b border-slate-900 border-l border-slate-800">
                  <span
                    className={`tabular-nums ${
                      gap != null && gap >= 0.03 ? "text-amber-400 font-semibold" : "text-slate-600"
                    }`}
                  >
                    {fmtPct(gap)}
                  </span>
                </td>
                <td className={`py-2 pl-2 text-right text-xs tabular-nums border-b border-slate-900 ${ageColor(age)}`}>
                  {fmtAge(age)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
