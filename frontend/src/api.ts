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
  subscribed: number;
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
  check_hours: number;
  ytdlp_auto_update: boolean;
};

export type PlexLibrary = { id: string; title: string; type: string; agent: string };
