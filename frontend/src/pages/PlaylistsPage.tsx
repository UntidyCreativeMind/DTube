import { ChevronDown, Clapperboard, Music, RefreshCw, Trash2, Tv } from "lucide-react";
import { useEffect, useState } from "react";
import { api, CHECK_FREQUENCIES, CheckFrequency, DownloadOptions, EPISODE_ORDER_HINT, EPISODE_ORDERS, EpisodeOrder, Source } from "../api";
import OptionsFields from "../components/OptionsFields";

const MODE_DETAILS = {
  tv: { icon: Tv, label: "TV show" },
  movie: { icon: Clapperboard, label: "Movies" },
  audio: { icon: Music, label: "Audio only" },
};

export default function PlaylistsPage() {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [message, setMessage] = useState("");
  const [checking, setChecking] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);

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

  const update = (source: Source, changes: { check_frequency?: CheckFrequency; episode_order?: EpisodeOrder; options?: Partial<DownloadOptions> }) =>
    api(`/sources/${source.id}`, "PATCH", changes).then(load).catch((e) => setMessage(e.message));

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
          {sources.map((source) => {
            const { icon: Icon, label } = MODE_DETAILS[source.mode];
            const expanded = open === source.id;
            return (
              <li key={source.id} className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-3">
                  <Icon size={18} className="text-muted" />
                  <div className="min-w-0 flex-1 basis-48">
                    <p className="truncate text-sm font-medium">{source.title}</p>
                    <p className="text-xs text-muted">
                      {label}
                      {source.last_checked ? ` · Checked ${new Date(source.last_checked * 1000).toLocaleString()}` : ""}
                    </p>
                  </div>
                  <button className="btn" onClick={() => setOpen(expanded ? null : source.id)} aria-expanded={expanded}>
                    Options <ChevronDown size={14} className={expanded ? "rotate-180" : ""} />
                  </button>
                  <button className="btn" onClick={() => check(source)} disabled={checking === source.id}>
                    <RefreshCw size={14} className={checking === source.id ? "animate-spin" : ""} /> Check now
                  </button>
                  <button className="p-1 text-muted hover:text-white" title="Stop tracking" aria-label="Stop tracking" onClick={() => remove(source)}>
                    <Trash2 size={17} />
                  </button>
                </div>
                {expanded && (
                  <div className="mt-3 space-y-3 border-t border-line pt-3">
                    <p className="text-xs text-muted">Changes apply to new downloads. Use History to replace videos you already have.</p>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <label className="block space-y-1 text-sm">
                        <span className="text-muted">Check for new videos</span>
                        <select className="field" value={source.check_frequency}
                          onChange={(e) => update(source, { check_frequency: e.target.value as CheckFrequency })}>
                          {CHECK_FREQUENCIES.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
                        </select>
                      </label>
                      {source.mode !== "movie" && (
                        <label className="block space-y-1 text-sm">
                          <span className="text-muted">{source.mode === "tv" ? "Episode order" : "Track order"}</span>
                          <select className="field" value={source.episode_order}
                            onChange={(e) => update(source, { episode_order: e.target.value as EpisodeOrder })}>
                            {EPISODE_ORDERS.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
                          </select>
                          <span className="block text-xs text-muted">{EPISODE_ORDER_HINT}</span>
                        </label>
                      )}
                    </div>
                    <OptionsFields value={source.options} onChange={(options) => update(source, { options })}
                      audioOnly={source.mode === "audio"} />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted">
          Playlists you download will appear here.
        </p>
      )}
    </div>
  );
}
