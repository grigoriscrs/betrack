import type { Sport, Status } from "../api";
import { ageColor, fmtAge, fmtAgo } from "../format";

interface BookSummary {
  name: string;
  events: number;
  last_observed: string | null;
  age_seconds: number | null;
  has_error: boolean;
}

function summarizeBooks(status: Status | null): BookSummary[] {
  if (!status) return [];
  return (status.bookmakers ?? []).map((name) => {
    let events = 0;
    for (const [key, c] of Object.entries(status.counts ?? {})) {
      if (!key.startsWith(name + "/")) continue;
      events += c.events;
    }
    const last_observed = status.book_last_observed?.[name] ?? null;
    const age_seconds = last_observed
      ? Math.max(0, Math.round((Date.now() - new Date(last_observed).getTime()) / 1000))
      : null;
    const has_error = (status.errors ?? []).some(
      (e) => e.toLowerCase().includes(name.toLowerCase())
    );
    return { name, events, last_observed, age_seconds, has_error };
  });
}

export function StatusBar({ status, sports }: { status: Status | null; sports: Sport[] }) {
  const fresh =
    status?.last_run != null &&
    Date.now() - new Date(status.last_run).getTime() < (status.poll_interval + 60) * 1000;

  const liveTotal = sports.reduce((n, s) => n + s.live_count, 0);
  const books = summarizeBooks(status);
  const subtitle = (status?.bookmakers ?? []).join(" + ") || "loading…";

  const topCards = [
    { label: "Live events", value: liveTotal || "–" },
    { label: "Quotes observed", value: status?.total_observed ?? "–" },
    { label: "Quotes changed", value: status?.total_changed ?? "–" },
    { label: "Detection", value: status?.detection ?? "–" },
  ];

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-white">
            BE<span className="text-emerald-400">Track</span>
          </h1>
          <span className="text-xs text-slate-500">live odds monitor · {subtitle}</span>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className={`h-2 w-2 rounded-full ${fresh ? "bg-emerald-400" : "bg-amber-400"}`} />
          <span className="text-slate-400">
            {status?.last_run ? `updated ${fmtAgo(status.last_run)}` : "waiting for first cycle…"}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3 text-sm">
        {topCards.map((c) => (
          <div key={c.label} className="bg-slate-900 border border-slate-800 rounded-lg px-3 py-2">
            <div className="text-slate-500 text-xs">{c.label}</div>
            <div className="text-white font-semibold truncate">{c.value}</div>
          </div>
        ))}
      </div>

      {books.length > 0 && (
        <div className="grid gap-2 mb-6 text-sm grid-cols-2 sm:grid-cols-3 md:grid-cols-3">
          {books.map((b) => {
            const dotColor = b.has_error
              ? "bg-red-400"
              : b.age_seconds == null
              ? "bg-slate-600"
              : b.age_seconds < 120
              ? "bg-emerald-400"
              : b.age_seconds < 600
              ? "bg-amber-400"
              : "bg-rose-500";
            const ageLabel = b.age_seconds == null ? "no data yet" : `${fmtAge(b.age_seconds)} ago`;
            return (
              <div
                key={b.name}
                className="bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 flex items-center gap-2"
                title={b.last_observed ? `last quote ${b.last_observed}` : "no quote_latest rows for this book"}
              >
                <span className={`h-2 w-2 rounded-full ${dotColor} shrink-0`} />
                <div className="flex-1 min-w-0">
                  <div className="text-white font-semibold leading-tight truncate">{b.name}</div>
                  <div className={`text-[11px] leading-tight ${ageColor(b.age_seconds)}`}>
                    {ageLabel} · {b.events} live
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {status?.errors && status.errors.length > 0 && (
        <div className="mb-4 px-3 py-2 rounded-md bg-red-950/40 border border-red-900/60 text-xs text-red-200">
          last cycle errors: {status.errors.join(" · ")}
        </div>
      )}
    </div>
  );
}
