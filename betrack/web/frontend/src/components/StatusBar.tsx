import type { Status } from "../api";
import { fmtAgo } from "../format";

export function StatusBar({ status }: { status: Status | null }) {
  const fresh =
    status?.last_run != null &&
    Date.now() - new Date(status.last_run).getTime() < (status.poll_interval + 60) * 1000;

  const cards = [
    { label: "Live events", value: status?.live ?? "–" },
    { label: "Prematch", value: status?.prematch ?? "–" },
    { label: "Covered", value: `${status?.covered ?? "–"} / ${status?.scanned ?? "–"}` },
    { label: "Quota left", value: status?.quota_remaining ?? "–" },
    { label: "Bookmakers", value: (status?.bookmakers ?? []).join(", ") || "–" },
  ];

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-white">
            BE<span className="text-emerald-400">Track</span>
          </h1>
          <span className="text-xs text-slate-500">odds discrepancy monitor</span>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className={`h-2 w-2 rounded-full ${fresh ? "bg-emerald-400" : "bg-amber-400"}`} />
          <span className="text-slate-400">
            {status?.last_run ? `updated ${fmtAgo(status.last_run)}` : "waiting for first cycle…"}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-6 text-sm">
        {cards.map((c) => (
          <div key={c.label} className="bg-slate-900 border border-slate-800 rounded-lg px-3 py-2">
            <div className="text-slate-500 text-xs">{c.label}</div>
            <div className="text-white font-semibold truncate">{c.value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
