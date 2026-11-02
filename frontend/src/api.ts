export async function api<T = any>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : "";
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return data as T;
}

export type AdLevel = "off" | "conservative" | "aggressive";

export const AD_LEVELS: { id: AdLevel; label: string; hint: string }[] = [
  { id: "off", label: "Off", hint: "Keeps the whole video." },
  { id: "conservative", label: "Conservative", hint: "Cuts self-promotion and subscribe reminders." },
  { id: "aggressive", label: "Aggressive", hint: "Also cuts paid sponsor segments." },
];

export type CheckFrequency = "off" | "immediate" | "hourly" | "daily";

export const CHECK_FREQUENCIES: { id: CheckFrequency; label: string }[] = [
  { id: "off", label: "Off" },
  { id: "immediate", label: "Immediate (every 5 minutes)" },
  { id: "hourly", label: "Hourly" },
  { id: "daily", label: "Daily" },
];

export type Job = {
  id: number;
  video_id: string;
  title: string;
  mode: "tv" | "movie";
  status: "queued" | "downloading" | "processing" | "done" | "failed" | "cancelled";
  progress: number;
  speed: number | null;
  eta: number | null;
  error: string | null;
};

export type Queue = { paused: string; jobs: Job[] };

export type Preview = {
  type: "video" | "playlist";
  title: string;
  channel: string;
  thumbnail: string;
  count: number;
  downloaded: number;
};

export type Source = {
  id: number;
  title: string;
  url: string;
  mode: "tv" | "movie";
  check_frequency: CheckFrequency;
  ad_removal: AdLevel;
  last_checked: number | null;
  done: number;
};

export type HistoryItem = {
  id: number;
  video_id: string;
  mode: "tv" | "movie";
  season: number | null;
  episode: number | null;
  path: string;
  missing: number;
  created_at: number;
  ad_removal: AdLevel;
  title: string;
  channel: string | null;
  show: string | null;
};

export type Settings = {
  temp_dir: string;
  tv_dir: string;
  movie_dir: string;
  plex_url: string;
  plex_token_set: boolean;
  plex_tv_section: string;
  plex_movie_section: string;
  plex_path_from: string;
  plex_path_to: string;
  file_uid: number;
  file_gid: number;
  quality: number;
  min_free_gb: number;
  ytdlp_auto_update: boolean;
  default_ad_removal: AdLevel;
};

export type PlexLibrary = { id: string; title: string; type: string; agent: string };
