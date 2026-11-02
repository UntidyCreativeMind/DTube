import { Download, ScanSearch } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { AD_LEVELS, AdLevel, api, HistoryItem } from "../api";

const PAGE_SIZE = 50;

export default function HistoryPage() {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ total: number; items: HistoryItem[] } | null>(null);
  const [message, setMessage] = useState("");
  const [redoing, setRedoing] = useState<number | null>(null);
  const [redoLevel, setRedoLevel] = useState<AdLevel>("off");

  const load = useCallback(() => {
    const params = new URLSearchParams({ q: query, page: String(page), size: String(PAGE_SIZE) });
    return api(`/history?${params}`).then(setData).catch((e) => setMessage(e.message));
  }, [query, page]);

  useEffect(() => {
    const timer = setTimeout(load, 200);
    return () => clearTimeout(timer);
  }, [load]);

  const startRedownload = (item: HistoryItem) => {
    setRedoLevel(item.ad_removal);
    setRedoing(redoing === item.id ? null : item.id);
  };

  const redownload = async (item: HistoryItem) => {
    setRedoing(null);
    try {
      const { queued } = await api(`/history/${item.id}/redownload`, "POST", { ad_removal: redoLevel });
      setMessage(queued ? `Queued "${item.title}".` : `"${item.title}" is already in the queue.`);
    } catch (err) {
      setMessage((err as Error).message);
    }
  };

  const scan = async () => {
    try {
      const { changed } = await api("/history/scan", "POST");
      setMessage(changed ? `Updated ${changed} ${changed === 1 ? "entry" : "entries"}.` : "All files are where they should be.");
      load();
    } catch (err) {
      setMessage((err as Error).message);
    }
  };

  const pages = data ? Math.max(Math.ceil(data.total / PAGE_SIZE), 1) : 1;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">History</h1>
        <button className="btn" onClick={scan}><ScanSearch size={15} /> Check for missing files</button>
      </div>
      <input className="field" placeholder="Search titles and shows" value={query}
        onChange={(e) => { setQuery(e.target.value); setPage(1); }} />
      {message && <p className="text-sm text-muted">{message}</p>}
      {data && data.items.length > 0 && (
        <ul className="divide-y divide-line rounded-2xl border border-line bg-panel">
          {data.items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{item.title}</p>
                <p className="truncate text-xs text-muted">
                  {item.mode === "tv"
                    ? `${item.show} · S${String(item.season).padStart(2, "0")}E${String(item.episode).padStart(2, "0")}`
                    : "Movie"}
                  {item.channel ? ` · ${item.channel}` : ""}
                  {item.ad_removal !== "off" ? ` · Ads: ${AD_LEVELS.find((l) => l.id === item.ad_removal)?.label}` : ""}
                  {item.missing ? <span className="text-accent-strong"> · File missing</span> : null}
                </p>
              </div>
              <button className="p-1 text-muted hover:text-white" title="Download again" aria-label="Download again"
                aria-expanded={redoing === item.id} onClick={() => startRedownload(item)}>
                <Download size={17} />
              </button>
              {redoing === item.id && (
                <div className="flex w-full flex-wrap items-center gap-2 text-sm text-muted">
                  <label className="flex items-center gap-2">
                    Ad removal
                    <select className="field !w-auto !py-1" value={redoLevel} onChange={(e) => setRedoLevel(e.target.value as AdLevel)}>
                      {AD_LEVELS.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
                    </select>
                  </label>
                  <button className="btn btn-primary" onClick={() => redownload(item)}>Replace the file</button>
                  <button className="btn" onClick={() => setRedoing(null)}>Cancel</button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {data && data.items.length === 0 && (
        <p className="rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted">
          {query ? "No matches." : "Downloaded videos will appear here."}
        </p>
      )}
      {pages > 1 && (
        <div className="flex items-center justify-center gap-3 text-sm text-muted">
          <button className="btn" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
          Page {page} of {pages}
          <button className="btn" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      )}
    </div>
  );
}
