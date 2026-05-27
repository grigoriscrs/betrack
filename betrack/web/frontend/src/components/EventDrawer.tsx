import { useEffect, useState } from "react";
import { api, type EventDetail } from "../api";
import { fmtOdds, fmtPct } from "../format";

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

  const books = detail?.bookmakers ?? [];

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
              {detail?.event_label ?? "Loading…"}
            </div>
            {detail?.competition && (
              <div className="text-xs text-slate-500">
                {detail.competition} · <span className="uppercase">{detail.status}</span>
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
              No snapshot available — this event isn't in the current store (it may have expired or
              the server restarted).
            </div>
          )}
          {!loading &&
            detail?.markets?.map((m) => (
              <div key={m.market_label}>
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
                      <th className="py-1.5 pr-3 text-right font-medium">Edge</th>
                    </tr>
                  </thead>
                  <tbody>
                    {m.outcomes.map((o) => {
                      const best = Math.max(...books.map((b) => o.quotes[b] ?? -Infinity));
                      return (
                        <tr key={o.outcome_type} className="border-b border-slate-900">
                          <td className="py-1.5 pr-3 text-slate-300">{o.outcome_label}</td>
                          {books.map((b) => {
                            const v = o.quotes[b];
                            const isBest = v != null && v === best && books.length > 1;
                            return (
                              <td
                                key={b}
                                className={`py-1.5 pr-3 text-right ${isBest ? "text-white font-semibold" : "text-slate-400"}`}
                              >
                                {fmtOdds(v)}
                              </td>
                            );
                          })}
                          <td
                            className={`py-1.5 pr-3 text-right ${
                              o.edge_pct != null && o.edge_pct >= 0.025 ? "text-emerald-400 font-semibold" : "text-slate-500"
                            }`}
                          >
                            {fmtPct(o.edge_pct)}
                          </td>
                        </tr>
                      );
                    })}
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
