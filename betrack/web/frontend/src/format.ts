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
