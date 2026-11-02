import { AlertTriangle, Clapperboard, Music, Tv } from "lucide-react";
import { ReactNode, useEffect, useMemo, useState } from "react";
import { api, Mode, Stats } from "../api";

const ACTIVITY_DAYS = 30;
const REFRESH_MS = 30000;
const MODE_ICONS = { tv: Tv, movie: Clapperboard, audio: Music };

function formatBytes(bytes: number) {
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 100 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function formatRuntime(seconds: number) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours >= 48) return `${Math.floor(hours / 24)}d ${hours % 24}h`;
  return hours ? `${hours}h ${minutes}m` : `${minutes}m`;
}

function plural(count: number, word: string) {
  return `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
}

function timeAgo(timestamp: number) {
  const minutes = Math.floor((Date.now() / 1000 - timestamp) / 60);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return `${Math.floor(minutes / 1440)}d ago`;
}

function Tile({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="min-w-0 rounded-2xl border border-line bg-panel p-4">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">{value}</p>
      {detail && <p className="mt-1 text-xs text-muted">{detail}</p>}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="min-w-0 rounded-2xl border border-line bg-panel p-4">
      <h2 className="mb-3 text-sm font-semibold text-muted">{title}</h2>
      {children}
    </section>
  );
}

function dayKey(date: Date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function useDailyCounts(activity: number[]) {
  return useMemo(() => {
    const counts = new Map<string, number>();
    for (const timestamp of activity) {
      const key = dayKey(new Date(timestamp * 1000));
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return Array.from({ length: ACTIVITY_DAYS }, (_, index) => {
      const date = new Date();
      date.setDate(date.getDate() - (ACTIVITY_DAYS - 1 - index));
      return {
        label: date.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
        count: counts.get(dayKey(date)) ?? 0,
      };
    });
  }, [activity]);
}

function ActivityChart({ days }: { days: { label: string; count: number }[] }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const peak = Math.max(...days.map((day) => day.count), 1);
  const shown = hovered === null ? null : days[hovered];

  return (
    <Panel title="Downloads per day">
      <p className="mb-2 h-5 text-sm" aria-live="polite">
        {shown ? `${shown.label}: ${plural(shown.count, "video")}` : <span className="text-muted">Busiest day: {plural(peak, "video")}</span>}
      </p>
      <div className="flex h-36 items-end gap-0.5 border-b border-line" role="img"
        aria-label={`Downloads per day for the last ${ACTIVITY_DAYS} days. The table below has the same figures.`}>
        {days.map((day, index) => (
          <div key={day.label} className="flex h-full min-w-0 flex-1 items-end"
            onMouseEnter={() => setHovered(index)} onMouseLeave={() => setHovered(null)}>
            {day.count > 0 && (
              <div className={`w-full rounded-t ${hovered === index ? "bg-white" : "bg-chart"}`}
                style={{ height: `${Math.max((day.count / peak) * 100, 3)}%` }} />
            )}
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-xs text-muted">
        <span>{days[0].label}</span>
        <span>Today</span>
      </div>
      <details className="mt-3 text-sm text-muted">
        <summary className="cursor-pointer text-xs">Show as a table</summary>
        <table className="mt-2 w-full text-xs">
          <thead><tr className="text-left"><th className="py-1 font-medium">Day</th><th className="py-1 text-right font-medium">Videos</th></tr></thead>
          <tbody>
            {days.filter((day) => day.count > 0).map((day) => (
              <tr key={day.label}><td className="py-0.5">{day.label}</td><td className="py-0.5 text-right">{day.count}</td></tr>
            ))}
          </tbody>
        </table>
      </details>
    </Panel>
  );
}

function TopChannels({ channels }: { channels: Stats["top_channels"] }) {
  const peak = Math.max(...channels.map((channel) => channel.count), 1);
  return (
    <Panel title="Top channels">
      {channels.length ? (
        <ul className="space-y-3">
          {channels.map(({ channel, count }) => (
            <li key={channel}>
              <div className="mb-1 flex justify-between gap-3 text-sm">
                <span className="min-w-0 truncate">{channel}</span>
                <span className="text-muted">{count.toLocaleString()}</span>
              </div>
              <div className="h-2 rounded-r bg-chart" style={{ width: `${Math.max((count / peak) * 100, 2)}%` }} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted">Channels appear here after your first download.</p>
      )}
    </Panel>
  );
}

function DiskMeter({ stats }: { stats: Stats }) {
  const { free_bytes: free, total_bytes: total } = stats.disk;
  if (free === null || total === null || total === 0) return null;
  const used = total - free;
  const dtube = Math.min(stats.storage.total_bytes, used);
  const other = used - dtube;
  const segments = [
    { label: "DTube", bytes: dtube, className: "bg-chart" },
    { label: "Other files", bytes: other, className: "bg-muted" },
  ];

  return (
    <Panel title="Disk space">
      <p className="text-sm"><span className="font-semibold">{formatBytes(free)}</span> <span className="text-muted">free of {formatBytes(total)}</span></p>
      <div className="mt-3 flex h-3 gap-0.5 overflow-hidden rounded bg-raised" role="img"
        aria-label={`DTube uses ${formatBytes(dtube)}, other files use ${formatBytes(other)}, and ${formatBytes(free)} is free.`}>
        {segments.map(({ label, bytes, className }) => bytes > 0 && (
          <div key={label} className={className} style={{ width: `${(bytes / total) * 100}%` }} />
        ))}
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        {segments.map(({ label, bytes, className }) => (
          <li key={label} className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-sm ${className}`} /> {label}: {formatBytes(bytes)}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function Attention({ stats, onNavigate }: { stats: Stats; onNavigate: (tab: string) => void }) {
  const items = [
    stats.queue.failed > 0 && { text: `${plural(stats.queue.failed, "download")} failed`, tab: "downloads" },
    stats.storage.missing > 0 && { text: `${plural(stats.storage.missing, "file")} missing from disk`, tab: "history" },
  ].filter(Boolean) as { text: string; tab: string }[];

  return (
    <>
      {items.map(({ text, tab }) => (
        <button key={text} onClick={() => onNavigate(tab)}
          className="flex w-full items-center gap-2 rounded-xl border border-line bg-panel px-4 py-3 text-left text-sm hover:border-muted">
          <AlertTriangle size={16} className="shrink-0 text-accent" /> {text}
        </button>
      ))}
    </>
  );
}

function RecentDownloads({ recent }: { recent: Stats["recent"] }) {
  return (
    <Panel title="Recent downloads">
      <ul className="space-y-2.5">
        {recent.map(({ title, mode, created_at }, index) => {
          const Icon = MODE_ICONS[mode as Mode];
          return (
            <li key={`${title}-${index}`} className="flex items-center gap-2.5 text-sm">
              <Icon size={15} className="shrink-0 text-muted" />
              <span className="min-w-0 flex-1 truncate">{title}</span>
              <span className="shrink-0 text-xs text-muted">{timeAgo(created_at)}</span>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

export default function HomePage({ onNavigate }: { onNavigate: (tab: string) => void }) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState("");
  const days = useDailyCounts(stats?.activity ?? []);

  useEffect(() => {
    const load = () => api<Stats>("/stats").then((s) => { setStats(s); setError(""); }).catch((e) => setError(e.message));
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, []);

  if (error && !stats) return <p className="text-sm text-accent-strong">{error}</p>;
  if (!stats) return null;

  const { videos, playlists, storage, runtime, queue } = stats;
  if (videos.total === 0 && queue.queued + queue.active === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-line px-4 py-16 text-center">
        <p className="font-semibold">Your library is empty</p>
        <p className="mt-1 text-sm text-muted">Add a playlist or video, and your stats will appear here.</p>
        <button className="btn btn-primary mt-4" onClick={() => onNavigate("downloads")}>Go to Downloads</button>
      </div>
    );
  }

  const lastWeek = days.slice(-7).reduce((total, day) => total + day.count, 0);
  const runtimeDetail = runtime.known < runtime.videos ? `From ${runtime.known.toLocaleString()} of ${plural(runtime.videos, "video")}` : "Before any ad cuts";
  const queueDetail = queue.active ? `${queue.active} downloading now` : "Nothing downloading";

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Home</h1>
      <Attention stats={stats} onNavigate={onNavigate} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile label="Videos" value={videos.total.toLocaleString()}
          detail={`${videos.tv.toLocaleString()} episodes · ${videos.movie.toLocaleString()} movies · ${videos.audio.toLocaleString()} audio`} />
        <Tile label="Total size" value={formatBytes(storage.total_bytes)}
          detail={`TV ${formatBytes(storage.tv_bytes)} · Movies ${formatBytes(storage.movie_bytes)} · Audio ${formatBytes(storage.audio_bytes)}`} />
        <Tile label="Playlists" value={playlists.total.toLocaleString()} detail={`${playlists.auto_checked.toLocaleString()} checked automatically`} />
        <Tile label="Total runtime" value={formatRuntime(runtime.seconds)} detail={runtimeDetail} />
        <Tile label="Channels" value={videos.channels.toLocaleString()} detail={`${plural(videos.unique, "unique video")}`} />
        <Tile label="Added this week" value={lastWeek.toLocaleString()} detail={`${stats.activity.length.toLocaleString()} in the last ${ACTIVITY_DAYS} days`} />
        <Tile label="Videos with ads removed" value={videos.ads_removed.toLocaleString()}
          detail={`${videos.total ? Math.round((videos.ads_removed / videos.total) * 100) : 0}% of your library`} />
        <Tile label="In the queue" value={queue.queued.toLocaleString()} detail={queueDetail} />
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <ActivityChart days={days} />
        <TopChannels channels={stats.top_channels} />
        <DiskMeter stats={stats} />
        {stats.recent.length > 0 && <RecentDownloads recent={stats.recent} />}
      </div>
    </div>
  );
}
