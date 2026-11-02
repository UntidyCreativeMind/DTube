import { AlertTriangle, Clapperboard, Loader2, RotateCcw, Tv, X } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import {
  AD_LEVELS, AdLevel, api, CHECK_FREQUENCIES, CheckFrequency, Job, Preview, QUALITIES, Quality, Queue, Settings,
} from "../api";

function useQueue() {
  const [queue, setQueue] = useState<Queue>({ paused: "", jobs: [] });

  useEffect(() => {
    let closed = false;
    let socket: WebSocket | null = null;
    let retry: number;
    const load = () => api<Queue>("/queue").then(setQueue).catch(() => {});

    const connect = () => {
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${location.host}/api/ws`);
      socket.onmessage = (event) => setQueue(JSON.parse(event.data));
      socket.onclose = () => {
        if (closed) return;
        load();
        retry = window.setTimeout(connect, 3000);
      };
    };

    load();
    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      socket?.close();
    };
  }, []);

  return queue;
}

function formatSpeed(bytes: number | null) {
  return bytes ? `${(bytes / 1024 / 1024).toFixed(1)} MB/s` : "";
}

function formatEta(seconds: number | null) {
  if (seconds === null) return "";
  const minutes = Math.floor(seconds / 60);
  return minutes ? `${minutes}m ${seconds % 60}s left` : `${seconds}s left`;
}

const STATUS_LABEL: Record<Job["status"], string> = {
  queued: "Queued",
  downloading: "Downloading",
  processing: "Filing into library",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

function JobRow({ job }: { job: Job }) {
  const active = job.status === "queued" || job.status === "downloading" || job.status === "processing";
  const retryable = job.status === "failed" || job.status === "cancelled";
  const detail = [job.status === "downloading" && `${job.progress.toFixed(0)}%`, formatSpeed(job.speed), formatEta(job.eta)]
    .filter(Boolean)
    .join(" · ");

  return (
    <li className="flex items-center gap-3 px-4 py-3">
      {job.mode === "tv" ? <Tv size={18} className="shrink-0 text-muted" /> : <Clapperboard size={18} className="shrink-0 text-muted" />}
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{job.title}</p>
        <p className={`truncate text-xs ${job.status === "failed" ? "text-accent-strong" : "text-muted"}`}>
          {job.error ? job.error : STATUS_LABEL[job.status]}
          {detail && ` · ${detail}`}
        </p>
        {job.status === "downloading" && (
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-raised">
            <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${job.progress}%` }} />
          </div>
        )}
      </div>
      {active && (
        <button className="p-1 text-muted hover:text-white" title="Cancel" aria-label="Cancel"
          onClick={() => api(`/jobs/${job.id}/cancel`, "POST").catch(() => {})}>
          <X size={18} />
        </button>
      )}
      {retryable && (
        <button className="p-1 text-muted hover:text-white" title="Try again" aria-label="Try again"
          onClick={() => api(`/jobs/${job.id}/retry`, "POST").catch(() => {})}>
          <RotateCcw size={18} />
        </button>
      )}
    </li>
  );
}

export default function DownloadsPage() {
  const queue = useQueue();
  const [url, setUrl] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mode, setMode] = useState<"tv" | "movie">("tv");
  const [checkFrequency, setCheckFrequency] = useState<CheckFrequency>("off");
  const [defaultAds, setDefaultAds] = useState<AdLevel>("off");
  const [adRemoval, setAdRemoval] = useState<AdLevel>("off");
  const [defaultQuality, setDefaultQuality] = useState<Quality>("1080");
  const [quality, setQuality] = useState<Quality>("1080");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<Settings>("/settings")
      .then((s) => {
        setDefaultAds(s.default_ad_removal);
        setAdRemoval(s.default_ad_removal);
        setDefaultQuality(s.default_quality);
        setQuality(s.default_quality);
      })
      .catch(() => {});
  }, []);

  const lookUp = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    setPreview(null);
    try {
      setPreview(await api<Preview>("/preview", "POST", { url }));
    } catch (err) {
      setError((err as Error).message);
    }
    setBusy(false);
  };

  const add = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await api("/sources", "POST", { url, mode, check_frequency: checkFrequency, ad_removal: adRemoval, quality });
      setMessage(result.added ? `Queued ${result.added} ${result.added === 1 ? "video" : "videos"}.` : "Nothing new to download.");
      setUrl("");
      setPreview(null);
      setCheckFrequency("off");
      setAdRemoval(defaultAds);
      setQuality(defaultQuality);
    } catch (err) {
      setError((err as Error).message);
    }
    setBusy(false);
  };

  const hasFinished = queue.jobs.some((j) => ["done", "failed", "cancelled"].includes(j.status));

  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-line bg-panel p-4 sm:p-5">
        <form onSubmit={lookUp} className="flex flex-col gap-2 sm:flex-row">
          <input className="field" type="url" placeholder="Paste a YouTube playlist or video URL" value={url}
            onChange={(e) => { setUrl(e.target.value); setPreview(null); }} required />
          <button className="btn btn-primary shrink-0" disabled={busy || !url}>
            {busy && !preview ? <Loader2 size={16} className="animate-spin" /> : null}
            Look up
          </button>
        </form>
        {error && <p className="mt-3 text-sm text-accent-strong">{error}</p>}
        {message && <p className="mt-3 text-sm text-muted">{message}</p>}

        {preview && (
          <div className="mt-4 flex flex-col gap-4 border-t border-line pt-4 sm:flex-row">
            {preview.thumbnail && (
              <img src={preview.thumbnail} alt="" referrerPolicy="no-referrer"
                className="aspect-video w-full rounded-lg object-cover sm:w-48" />
            )}
            <div className="min-w-0 flex-1 space-y-3">
              <div>
                <p className="font-semibold">{preview.title}</p>
                <p className="text-sm text-muted">
                  {[preview.channel, preview.type === "playlist" && `${preview.count} videos`,
                    preview.downloaded > 0 && `${preview.downloaded} already downloaded`].filter(Boolean).join(" · ")}
                </p>
              </div>
              {preview.type === "playlist" ? (
                <div className="flex flex-col gap-3">
                  <div className="inline-flex w-fit rounded-lg border border-line p-0.5">
                    {([["tv", "TV show", Tv], ["movie", "Movies", Clapperboard]] as const).map(([id, label, Icon]) => (
                      <button key={id} type="button" onClick={() => setMode(id)}
                        className={`flex items-center gap-2 rounded-md px-3 py-1.5 text-sm ${mode === id ? "bg-raised text-white" : "text-muted"}`}>
                        <Icon size={15} /> {label}
                      </button>
                    ))}
                  </div>
                  <label className="flex flex-wrap items-center gap-2 text-sm text-muted">
                    Check for new videos
                    <select className="field !w-auto !py-1" value={checkFrequency}
                      onChange={(e) => setCheckFrequency(e.target.value as CheckFrequency)}>
                      {CHECK_FREQUENCIES.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
                    </select>
                  </label>
                </div>
              ) : (
                <p className="flex items-center gap-2 text-sm text-muted"><Clapperboard size={15} /> Single videos download as movies</p>
              )}
              <label className="flex flex-wrap items-center gap-2 text-sm text-muted">
                Quality
                <select className="field !w-auto !py-1" value={quality} onChange={(e) => setQuality(e.target.value as Quality)}>
                  {QUALITIES.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
                </select>
              </label>
              <div className="space-y-1.5">
                <p className="text-sm text-muted">Ad removal</p>
                <div className="inline-flex max-w-full flex-wrap rounded-lg border border-line p-0.5">
                  {AD_LEVELS.map(({ id, label }) => (
                    <button key={id} type="button" onClick={() => setAdRemoval(id)} aria-pressed={adRemoval === id}
                      className={`rounded-md px-3 py-1.5 text-sm ${adRemoval === id ? "bg-raised text-white" : "text-muted"}`}>
                      {label}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-muted">{AD_LEVELS.find((l) => l.id === adRemoval)!.hint}</p>
              </div>
              <button className="btn btn-primary" onClick={add} disabled={busy}>Download</button>
            </div>
          </div>
        )}
      </section>

      {queue.paused && (
        <p className="flex items-center gap-2 rounded-xl border border-line bg-panel px-4 py-3 text-sm">
          <AlertTriangle size={16} className="shrink-0 text-accent" /> {queue.paused}
        </p>
      )}

      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-muted">Queue</h2>
          {hasFinished && (
            <button className="text-xs text-muted hover:text-white" onClick={() => api("/jobs/finished", "DELETE").catch(() => {})}>
              Clear finished
            </button>
          )}
        </div>
        {queue.jobs.length ? (
          <ul className="divide-y divide-line rounded-2xl border border-line bg-panel">
            {queue.jobs.map((job) => <JobRow key={job.id} job={job} />)}
          </ul>
        ) : (
          <p className="rounded-2xl border border-dashed border-line px-4 py-10 text-center text-sm text-muted">
            Nothing queued. Paste a URL above to start.
          </p>
        )}
      </section>
    </div>
  );
}
