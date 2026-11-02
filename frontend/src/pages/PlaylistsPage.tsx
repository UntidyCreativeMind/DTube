import { Clapperboard, RefreshCw, Trash2, Tv } from "lucide-react";
import { useEffect, useState } from "react";
import { AD_LEVELS, AdLevel, api, CHECK_FREQUENCIES, CheckFrequency, Source } from "../api";

export default function PlaylistsPage() {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [message, setMessage] = useState("");
  const [checking, setChecking] = useState<number | null>(null);

  const load = () => api<Source[]>("/sources").then(setSources).catch((e) => setMessage(e.message));
  useEffect(() => {
    load();
  }, []);

  const check = async (source: Source) => {
    setChecking(source.id);
    setMessage("");
    try {
      const { added } = await api(`/sources/${source.id}/check`, "POST");
      setMessage(`${source.title}: ${added ? `queued ${added} new` : "nothing new"}.`);
    } catch (err) {
      setMessage((err as Error).message);
    }
    setChecking(null);
    load();
  };

  const setCheckFrequency = (source: Source, check_frequency: CheckFrequency) =>
    api(`/sources/${source.id}`, "PATCH", { check_frequency }).then(load).catch((e) => setMessage(e.message));

  const setAdRemoval = (source: Source, ad_removal: AdLevel) =>
    api(`/sources/${source.id}`, "PATCH", { ad_removal }).then(load).catch((e) => setMessage(e.message));

  const remove = (source: Source) => {
    if (confirm(`Stop tracking "${source.title}"? Downloaded files and history are kept.`)) {
      api(`/sources/${source.id}`, "DELETE").then(load).catch((e) => setMessage(e.message));
    }
  };

  if (!sources) return null;

  return (
    <div className="space-y-3">
      <h1 className="text-xl font-semibold">Playlists</h1>
      {message && <p className="text-sm text-muted">{message}</p>}
      {sources.length ? (
        <ul className="divide-y divide-line rounded-2xl border border-line bg-panel">
          {sources.map((source) => (
            <li key={source.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
              {source.mode === "tv" ? <Tv size={18} className="text-muted" /> : <Clapperboard size={18} className="text-muted" />}
              <div className="min-w-0 flex-1 basis-48">
                <p className="truncate text-sm font-medium">{source.title}</p>
                <p className="text-xs text-muted">
                  {source.mode === "tv" ? "TV show" : "Movies"}
                  {source.last_checked ? ` · Checked ${new Date(source.last_checked * 1000).toLocaleString()}` : ""}
                </p>
              </div>
              <label className="flex items-center gap-2 text-xs text-muted">
                Ad removal
                <select className="field !w-auto !py-1 text-xs" value={source.ad_removal} title="Applies to new downloads"
                  onChange={(e) => setAdRemoval(source, e.target.value as AdLevel)}>
                  {AD_LEVELS.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
                </select>
              </label>
              <label className="flex items-center gap-2 text-xs text-muted">
                Check for new videos
                <select className="field !w-auto !py-1 text-xs" value={source.check_frequency}
                  onChange={(e) => setCheckFrequency(source, e.target.value as CheckFrequency)}>
                  {CHECK_FREQUENCIES.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
                </select>
              </label>
              <button className="btn" onClick={() => check(source)} disabled={checking === source.id}>
                <RefreshCw size={14} className={checking === source.id ? "animate-spin" : ""} /> Check now
              </button>
              <button className="p-1 text-muted hover:text-white" title="Stop tracking" aria-label="Stop tracking" onClick={() => remove(source)}>
                <Trash2 size={17} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted">
          Playlists you download will appear here.
        </p>
      )}
    </div>
  );
}
