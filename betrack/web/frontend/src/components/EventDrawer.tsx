import { useEffect, useState } from "react";
import { api, type EventDetail } from "../api";
import { ageColor, fmtAge, fmtOdds, fmtPct } from "../format";

interface Props {
  eventId: string | null;
  onClose: () => void;
}

export function EventDrawer({ eventId, onClose }: Props) {
  const [detail, setDetail] = useState<EventDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!eventId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .event(eventId)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setDetail({ found: false }))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  if (!eventId) return null;

  const books = detail?.books ?? [];

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60" />
      <div
        className="relative w-full max-w-2xl h-full bg-slate-950 border-l border-slate-800 shadow-2xl overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-slate-950/95 backdrop-blur border-b border-slate-800 px-5 py-4 flex items-start justify-between">
          <div>
            <div className="text-lg font-semibold text-white">
              {detail?.found ? `${detail.home_team} v ${detail.away_team}` : "Loading…"}
            </div>
            {detail?.found && (
              <div className="text-xs text-slate-500">
                {detail.competition} · <span className="uppercase">{detail.status}</span>
                {detail.sportradar_match_id ? ` · SR ${detail.sportradar_match_id}` : ""}
              </div>
            )}
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white text-xl leading-none">
            ×
          </button>
        </div>

        <div className="p-5 space-y-6">
          {loading && <div className="text-slate-500 text-sm">Loading snapshot…</div>}
          {!loading && detail && !detail.found && (
            <div className="text-slate-500 text-sm">
              No snapshot — this event isn't in the current store (it may have expired or the server
              restarted).
            </div>
          )}
          {!loading &&
            detail?.markets?.map((m) => (
              <div key={`${m.market_type}:${m.period}:${m.line}`}>
                <div className="text-sm font-medium text-slate-300 mb-2">{m.market_label}</div>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-slate-500 text-xs uppercase tracking-wider border-b border-slate-800">
                      <th className="py-1.5 pr-3 text-left font-medium">Outcome</th>
                      {books.map((b) => (
                        <th key={b} className="py-1.5 pr-3 text-right font-medium">
                          {b}
                        </th>
                      ))}
                      <th className="py-1.5 pr-3 text-right font-medium">Gap</th>
                    </tr>
                  </thead>
                  <tbody>
                    {m.outcomes.map((o) => (
                      <tr key={`${o.outcome_type}:${o.line}`} className="border-b border-slate-900">
                        <td className="py-1.5 pr-3 text-slate-300">{o.label}</td>
                        {books.map((b) => {
                          const q = o.quotes[b];
                          const isBest = o.best === b;
                          return (
                            <td key={b} className="py-1.5 pr-3 text-right">
                              <div className={isBest ? "text-emerald-300 font-semibold" : "text-slate-400"}>
                                {fmtOdds(q?.odds)}
                              </div>
                              {q && (
                                <div className={`text-[10px] ${ageColor(q.age_seconds)}`}>
                                  {fmtAge(q.age_seconds)} ago
                                </div>
                              )}
                            </td>
                          );
                        })}
                        <td
                          className={`py-1.5 pr-3 text-right ${
                            o.gap_pct != null && o.gap_pct >= 0.03
                              ? "text-amber-400 font-semibold"
                              : "text-slate-600"
                          }`}
                        >
                          {fmtPct(o.gap_pct)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          {!loading && detail?.found && (detail.markets?.length ?? 0) === 0 && (
            <div className="text-slate-500 text-sm">No markets recorded for this event yet.</div>
          )}
        </div>
      </div>
    </div>
  );
}
