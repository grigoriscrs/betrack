export function fmtOdds(v: number | null | undefined): string {
  return v == null ? "—" : Number(v).toFixed(2);
}

export function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return (v >= 0 ? "+" : "") + (v * 100).toFixed(2) + "%";
}

export function fmtDuration(sec: number | null | undefined): string {
  if (!sec || sec < 1) return "just now";
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const sec = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  return fmtDuration(sec) + " ago";
}

// Short form for quote freshness (e.g. "12s", "4m", "1h"); "?" if missing.
export function fmtAge(sec: number | null | undefined): string {
  if (sec == null) return "?";
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  return `${Math.floor(sec / 3600)}h`;
}

// Tailwind color class by quote-age tier: fresh / aging / stale.
export function ageColor(sec: number | null | undefined): string {
  if (sec == null) return "text-slate-500";
  if (sec < 120) return "text-emerald-400";
  if (sec < 600) return "text-amber-400";
  return "text-red-400";
}

// Background + text combo for the bookmaker chip column (EventsTable / drawer).
// "absent" = book has no quote on this row at all.
export function bookChipClass(sec: number | null | undefined, absent: boolean): string {
  if (absent) return "bg-slate-800 text-slate-600";
  if (sec == null) return "bg-slate-700/60 text-slate-300";
  if (sec < 120) return "bg-emerald-900/70 text-emerald-300";
  if (sec < 600) return "bg-amber-900/70 text-amber-300";
  return "bg-rose-900/70 text-rose-300";
}
