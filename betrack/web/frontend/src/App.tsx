import { useEffect, useMemo, useState } from "react";
import { api, edgeValue, type Opportunity, type Status } from "./api";
import { StatusBar } from "./components/StatusBar";
import { DEFAULT_FILTERS, FilterBar, type Filters } from "./components/FilterBar";
import { OpportunitiesTable, type Sort, type SortKey } from "./components/OpportunitiesTable";
import { EventDrawer } from "./components/EventDrawer";

type Tab = "live" | "history";

function applyFilters(rows: Opportunity[], f: Filters, mode: Tab): Opportunity[] {
  const needle = f.search.trim().toLowerCase();
  return rows.filter((o) => {
    if (needle && !`${o.event_label} ${o.competition}`.toLowerCase().includes(needle)) return false;
    if (f.kind !== "all" && o.kind !== f.kind) return false;
    if (f.market !== "all" && o.market_label !== f.market) return false;
    if (f.bookmaker !== "all") {
      const inValue = o.bookmaker === f.bookmaker;
      const inArb = o.legs ? Object.values(o.legs).some((l) => l.bookmaker === f.bookmaker) : false;
      if (!inValue && !inArb) return false;
    }
    if (mode === "history" && f.status !== "all") {
      if (f.status === "active" && !o.active) return false;
      if (f.status === "expired" && o.active) return false;
    }
    const edge = edgeValue(o);
    if (f.minEdge > 0 && (edge == null || edge * 100 < f.minEdge)) return false;
    return true;
  });
}

function sortRows(rows: Opportunity[], sort: Sort): Opportunity[] {
  const dir = sort.dir === "asc" ? 1 : -1;
  const val = (o: Opportunity): number | string => {
    switch (sort.key) {
      case "event":
        return o.event_label.toLowerCase();
      case "market":
        return o.market_label.toLowerCase();
      case "edge":
        return edgeValue(o) ?? -Infinity;
      case "duration":
        return o.duration_seconds;
      case "lastSeen":
        return new Date(o.last_seen).getTime();
      case "status":
        return o.active ? 1 : 0;
    }
  };
  return [...rows].sort((a, b) => {
    const av = val(a);
    const bv = val(b);
    if (av < bv) return -1 * dir;
    if (av > bv) return 1 * dir;
    return 0;
  });
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [history, setHistory] = useState<Opportunity[]>([]);
  const [tab, setTab] = useState<Tab>("live");
  const [filters, setFilters] = useState<Filters>({ ...DEFAULT_FILTERS });
  const [sort, setSort] = useState<Sort>({ key: "edge", dir: "desc" });
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const [st, opps, hist] = await Promise.all([api.status(), api.opportunities(), api.history()]);
        if (stop) return;
        setStatus(st);
        setOpportunities(opps);
        setHistory(hist);
      } catch {
        /* transient; keep last good state */
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, []);

  const source = tab === "live" ? opportunities : history;

  const markets = useMemo(
    () => Array.from(new Set(source.map((o) => o.market_label))).sort(),
    [source]
  );
  const bookmakers = status?.bookmakers ?? [];

  const visible = useMemo(
    () => sortRows(applyFilters(source, filters, tab), sort),
    [source, filters, tab, sort]
  );

  const onSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));

  const tabBtn = (id: Tab, label: string, count?: number) => (
    <button
      onClick={() => setTab(id)}
      className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
        tab === id
          ? "border-emerald-400 text-white"
          : "border-transparent text-slate-400 hover:text-slate-200"
      }`}
    >
      {label}
      {count != null && <span className="ml-1 text-xs text-slate-500">({count})</span>}
    </button>
  );

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      <StatusBar status={status} />

      <div className="flex gap-1 mb-4 border-b border-slate-800">
        {tabBtn("live", "Live Opportunities", opportunities.length)}
        {tabBtn("history", "History")}
      </div>

      <FilterBar
        filters={filters}
        onChange={setFilters}
        markets={markets}
        bookmakers={bookmakers}
        showStatus={tab === "history"}
      />

      <OpportunitiesTable rows={visible} mode={tab} sort={sort} onSort={onSort} onSelect={setSelected} />

      <p className="text-xs text-slate-600 mt-8">
        Reference line: {status?.reference ?? "Stoiximan"} (soft stand-in for a sharp line). Data via
        odds-api.io free tier. Click any row to inspect the full event.
      </p>

      <EventDrawer eventId={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
