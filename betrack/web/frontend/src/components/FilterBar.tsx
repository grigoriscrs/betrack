export interface Filters {
  search: string;
  kind: "all" | "value" | "arb";
  market: string; // market_label or "all"
  bookmaker: string; // bookmaker or "all"
  status: "all" | "active" | "expired"; // history only
  minEdge: number; // percent
}

export const DEFAULT_FILTERS: Filters = {
  search: "",
  kind: "all",
  market: "all",
  bookmaker: "all",
  status: "all",
  minEdge: 0,
};

interface Props {
  filters: Filters;
  onChange: (f: Filters) => void;
  markets: string[];
  bookmakers: string[];
  showStatus: boolean;
}

const selectCls =
  "bg-slate-900 border border-slate-800 rounded-md px-2 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-emerald-500";

export function FilterBar({ filters, onChange, markets, bookmakers, showStatus }: Props) {
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });

  return (
    <div className="flex flex-wrap items-center gap-2 mb-4">
      <input
        type="text"
        placeholder="Search event / competition…"
        value={filters.search}
        onChange={(e) => set({ search: e.target.value })}
        className={`${selectCls} w-56`}
      />

      <select value={filters.kind} onChange={(e) => set({ kind: e.target.value as Filters["kind"] })} className={selectCls}>
        <option value="all">All types</option>
        <option value="value">Value</option>
        <option value="arb">Arbitrage</option>
      </select>

      <select value={filters.market} onChange={(e) => set({ market: e.target.value })} className={selectCls}>
        <option value="all">All markets</option>
        {markets.map((m) => (
          <option key={m} value={m}>{m}</option>
        ))}
      </select>

      <select value={filters.bookmaker} onChange={(e) => set({ bookmaker: e.target.value })} className={selectCls}>
        <option value="all">All bookmakers</option>
        {bookmakers.map((b) => (
          <option key={b} value={b}>{b}</option>
        ))}
      </select>

      {showStatus && (
        <select value={filters.status} onChange={(e) => set({ status: e.target.value as Filters["status"] })} className={selectCls}>
          <option value="all">Any status</option>
          <option value="active">Active</option>
          <option value="expired">Expired</option>
        </select>
      )}

      <label className="flex items-center gap-2 text-sm text-slate-400">
        min edge
        <input
          type="number"
          min={0}
          step={0.5}
          value={filters.minEdge}
          onChange={(e) => set({ minEdge: Number(e.target.value) || 0 })}
          className={`${selectCls} w-20`}
        />
        %
      </label>

      <button
        onClick={() => onChange({ ...DEFAULT_FILTERS })}
        className="text-xs text-slate-500 hover:text-slate-300 underline underline-offset-2"
      >
        reset
      </button>
    </div>
  );
}
