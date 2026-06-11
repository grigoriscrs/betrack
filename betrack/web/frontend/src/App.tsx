import { useEffect, useMemo, useState } from "react";
import { api, type EventRow, type Sport, type Status } from "./api";
import { StatusBar } from "./components/StatusBar";
import { EventsTable } from "./components/EventsTable";
import { EventDrawer } from "./components/EventDrawer";
import { OpportunitiesView } from "./components/OpportunitiesView";

type Page = "opportunities" | "events";

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [sports, setSports] = useState<Sport[]>([]);
  const [page, setPage] = useState<Page>("opportunities");
  const [sport, setSport] = useState<string>("football");
  const [events, setEvents] = useState<EventRow[]>([]);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const allBookmakers = status?.bookmakers ?? ["Stoiximan", "Novibet", "Pamestoixima", "Betfair"];
  // Multi-select bookmaker filter. Initialise to all once status arrives.
  const [selectedBooks, setSelectedBooks] = useState<string[] | null>(null);
  useEffect(() => {
    if (selectedBooks === null && status?.bookmakers) {
      setSelectedBooks(status.bookmakers);
    }
  }, [status, selectedBooks]);
  const effectiveSelected = selectedBooks ?? allBookmakers;

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        if (page === "events") {
          const [st, sp, ev] = await Promise.all([api.status(), api.sports(), api.events(sport)]);
          if (stop) return;
          setStatus(st);
          setSports(sp);
          setEvents(ev);
        } else {
          const [st, sp] = await Promise.all([api.status(), api.sports()]);
          if (stop) return;
          setStatus(st);
          setSports(sp);
        }
      } catch {
        /* transient */
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [page, sport]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return events.filter((e) => {
      if (needle && !`${e.home_team} ${e.away_team} ${e.competition}`.toLowerCase().includes(needle))
        return false;
      // Multi-select: keep the event if it has at least one of the selected books.
      if (!effectiveSelected.some((b) => e.books.includes(b))) return false;
      return true;
    });
  }, [events, search, effectiveSelected]);

  const selectCls =
    "bg-slate-900 border border-slate-800 rounded-md px-2 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-emerald-500";

  const pageBtn = (key: Page, label: string) => (
    <button
      key={key}
      onClick={() => setPage(key)}
      className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
        page === key
          ? "border-emerald-400 text-white"
          : "border-transparent text-slate-400 hover:text-slate-200"
      }`}
    >
      {label}
    </button>
  );

  const sportBtn = (s: Sport) => (
    <button
      key={s.key}
      onClick={() => setSport(s.key)}
      className={`px-3 py-1.5 text-xs font-semibold uppercase tracking-wider rounded-md border transition-colors ${
        sport === s.key
          ? "border-emerald-400 text-white bg-emerald-400/10"
          : "border-slate-800 text-slate-500 hover:text-slate-300"
      }`}
    >
      {s.label}
      <span className="ml-1 opacity-60">({s.live_count})</span>
    </button>
  );

  const toggleBook = (b: string) => {
    if (selectedBooks === null) {
      setSelectedBooks(allBookmakers.filter((x) => x !== b));
      return;
    }
    if (selectedBooks.includes(b)) {
      setSelectedBooks(selectedBooks.filter((x) => x !== b));
    } else {
      setSelectedBooks([...selectedBooks, b]);
    }
  };

  const bookChip = (b: string) => {
    const on = effectiveSelected.includes(b);
    return (
      <button
        key={b}
        onClick={() => toggleBook(b)}
        className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-colors ${
          on
            ? "border-emerald-400 text-emerald-300 bg-emerald-400/10"
            : "border-slate-800 text-slate-500 hover:text-slate-300"
        }`}
      >
        {b}
      </button>
    );
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      <StatusBar status={status} sports={sports} />

      <div className="flex gap-1 mb-4 border-b border-slate-800">
        {pageBtn("opportunities", "Opportunities")}
        {pageBtn("events", "Events")}
      </div>

      {page === "opportunities" && <OpportunitiesView onSelect={setSelected} />}

      {page === "events" && (
        <>
          <div className="flex flex-wrap items-center gap-2 mb-3">
            {(sports.length ? sports : [{ key: "football", label: "Football", live_count: 0 }]).map(sportBtn)}
            <span className="ml-2 text-xs text-slate-600">·</span>
            <span className="text-xs text-slate-500 mr-1">Books:</span>
            {allBookmakers.map(bookChip)}
            <button
              onClick={() => setSelectedBooks(allBookmakers)}
              className="px-2 py-1 text-[11px] text-slate-500 hover:text-slate-300"
              title="select all"
            >
              all
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2 mb-4">
            <input
              type="text"
              placeholder="Search team / competition…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className={`${selectCls} w-72`}
            />
            <span className="text-xs text-slate-600 ml-auto">
              {visible.length} event{visible.length === 1 ? "" : "s"} · showing {effectiveSelected.length} of{" "}
              {allBookmakers.length} books
            </span>
          </div>
          <EventsTable
            rows={visible}
            sport={sport}
            selectedBookmakers={effectiveSelected}
            onSelect={setSelected}
          />
          <p className="text-xs text-slate-600 mt-6">
            Live odds polled directly from {allBookmakers.join(" + ")} every {status?.poll_interval ?? 30}s.
            Stoiximan/Novibet are matched by Sportradar id; Betfair cross-matches by team names. Click a row
            for the full per-market drawer.
          </p>
        </>
      )}

      <EventDrawer eventId={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
