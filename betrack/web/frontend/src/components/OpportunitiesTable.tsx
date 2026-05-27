import type { Opportunity } from "../api";
import { edgeValue } from "../api";
import { fmtDuration, fmtOdds, fmtPct, fmtTime } from "../format";

export type SortKey = "event" | "market" | "edge" | "duration" | "lastSeen" | "status";
export interface Sort {
  key: SortKey;
  dir: "asc" | "desc";
}

interface Props {
  rows: Opportunity[];
  mode: "live" | "history";
  sort: Sort;
  onSort: (key: SortKey) => void;
  onSelect: (eventId: string) => void;
}

function Th({
  label,
  sortKey,
  sort,
  onSort,
  align = "left",
}: {
  label: string;
  sortKey?: SortKey;
  sort: Sort;
  onSort: (k: SortKey) => void;
  align?: "left" | "right";
}) {
  const active = sortKey && sort.key === sortKey;
  const arrow = active ? (sort.dir === "asc" ? " ↑" : " ↓") : "";
  return (
    <th
      className={`py-2 pr-3 font-medium ${align === "right" ? "text-right" : "text-left"} ${
        sortKey ? "cursor-pointer select-none hover:text-slate-300" : ""
      }`}
      onClick={() => sortKey && onSort(sortKey)}
    >
      {label}
      <span className="text-emerald-400">{arrow}</span>
    </th>
  );
}

export function OpportunitiesTable({ rows, mode, sort, onSort, onSelect }: Props) {
  if (rows.length === 0) {
    return <div className="text-center text-slate-500 py-16 text-sm">No rows match the current filters.</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-slate-500 text-xs uppercase tracking-wider border-b border-slate-800">
            {mode === "history" && <Th label="Status" sortKey="status" sort={sort} onSort={onSort} />}
            <th className="py-2 pr-3 text-left font-medium">Type</th>
            <Th label="Event" sortKey="event" sort={sort} onSort={onSort} />
            <Th label="Market" sortKey="market" sort={sort} onSort={onSort} />
            <th className="py-2 pr-3 text-left font-medium">Outcome</th>
            {mode === "live" && <th className="py-2 pr-3 text-right font-medium">Ref</th>}
            {mode === "live" && <th className="py-2 pr-3 text-right font-medium">Book</th>}
            <Th label="Edge" sortKey="edge" sort={sort} onSort={onSort} align="right" />
            {mode === "live" ? (
              <Th label="Seen" sortKey="duration" sort={sort} onSort={onSort} align="right" />
            ) : (
              <>
                <Th label="Duration" sortKey="duration" sort={sort} onSort={onSort} align="right" />
                <Th label="Last seen" sortKey="lastSeen" sort={sort} onSort={onSort} align="right" />
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((o) => (
            <tr
              key={o.id}
              onClick={() => onSelect(o.event_id)}
              className="border-b border-slate-900 hover:bg-slate-900/60 cursor-pointer"
            >
              {mode === "history" && (
                <td className="py-2 pr-3">
                  <span
                    className={`text-xs px-1.5 py-0.5 rounded ${
                      o.active ? "bg-emerald-900/60 text-emerald-300" : "bg-slate-800 text-slate-500"
                    }`}
                  >
                    {o.active ? "ACTIVE" : "expired"}
                  </span>
                </td>
              )}
              <td className="py-2 pr-3 whitespace-nowrap">
                <span
                  className={`text-xs px-1.5 py-0.5 rounded ${
                    o.kind === "arb" ? "bg-purple-900/60 text-purple-300" : "bg-emerald-900/60 text-emerald-300"
                  }`}
                >
                  {o.kind === "arb" ? "ARB" : "VALUE"}
                </span>
                {o.alerted && (
                  <span title="Confirmed (persistence + cooldown passed)" className="ml-1 text-amber-400">
                    ★
                  </span>
                )}
              </td>
              <td className="py-2 pr-3">
                <div className="text-white">{o.event_label}</div>
                <div className="text-xs text-slate-500">{o.competition}</div>
              </td>
              <td className="py-2 pr-3 text-slate-300 whitespace-nowrap">{o.market_label}</td>
              <td className="py-2 pr-3 text-slate-300">
                {o.kind === "value" ? (
                  o.outcome_label
                ) : (
                  <div className="text-xs space-y-0.5">
                    {Object.entries(o.legs ?? {}).map(([name, leg]) => (
                      <div key={name}>
                        <span className="text-slate-400">{name}</span>: {leg.bookmaker} @ {leg.odds}
                      </div>
                    ))}
                  </div>
                )}
              </td>
              {mode === "live" && (
                <td className="py-2 pr-3 text-right text-slate-400">
                  {o.kind === "value" ? fmtOdds(o.reference_odds) : "—"}
                </td>
              )}
              {mode === "live" && (
                <td className="py-2 pr-3 text-right text-white">
                  {o.kind === "value" ? fmtOdds(o.bookmaker_odds) : "—"}
                </td>
              )}
              <td className="py-2 pr-3 text-right font-semibold text-emerald-400">{fmtPct(edgeValue(o))}</td>
              {mode === "live" ? (
                <td className="py-2 pr-3 text-right text-slate-500 text-xs">{fmtDuration(o.duration_seconds)}</td>
              ) : (
                <>
                  <td className="py-2 pr-3 text-right text-slate-500 text-xs">{fmtDuration(o.duration_seconds)}</td>
                  <td className="py-2 pr-3 text-right text-slate-500 text-xs">{fmtTime(o.last_seen)}</td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
