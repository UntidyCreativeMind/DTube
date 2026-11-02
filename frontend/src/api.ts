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

export type Quality = "max" | "1080" | "720" | "480" | "360";

export const QUALITIES: { id: Quality; label: string }[] = [
  { id: "max", label: "Max available" },
  { id: "1080", label: "1080p" },
  { id: "720", label: "720p" },
  { id: "480", label: "480p" },
  { id: "360", label: "360p" },
];

export type SubtitleMode = "off" | "manual" | "auto";

export const SUBTITLE_MODES: { id: SubtitleMode; label: string }[] = [
  { id: "off", label: "Off" },
  { id: "manual", label: "Creator-made only" },
  { id: "auto", label: "Creator-made or auto-generated" },
];

const LANGUAGES = [
  { id: "en", label: "English" },
  { id: "es", label: "Spanish" },
  { id: "fr", label: "French" },
  { id: "de", label: "German" },
  { id: "it", label: "Italian" },
  { id: "pt", label: "Portuguese" },
  { id: "ja", label: "Japanese" },
  { id: "ko", label: "Korean" },
  { id: "zh", label: "Chinese" },
  { id: "hi", label: "Hindi" },
  { id: "ar", label: "Arabic" },
  { id: "ru", label: "Russian" },
];

export const SUBTITLE_LANGUAGES = LANGUAGES;
export const AUDIO_LANGUAGES = [{ id: "original", label: "Original" }, ...LANGUAGES];

export type DownloadOptions = {
  quality: Quality;
  ad_removal: AdLevel;
  subtitles: SubtitleMode;
  subtitle_lang: string;
  audio_lang: string;
  chapters: boolean;
};

export const DEFAULT_OPTIONS: DownloadOptions = {
  quality: "1080",
  ad_removal: "off",
  subtitles: "off",
  subtitle_lang: "en",
  audio_lang: "original",
  chapters: true,
};

export type EpisodeOrder = "playlist" | "reverse";

export const EPISODE_ORDERS: { id: EpisodeOrder; label: string }[] = [
  { id: "playlist", label: "Playlist order" },
  { id: "reverse", label: "Reverse playlist order" },
];

export const EPISODE_ORDER_HINT = "Use reverse for playlists that list the newest video first. Numbers already given don't change.";

export type Mode = "tv" | "movie" | "audio";

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
  mode: Mode;
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
  mode: Mode;
  check_frequency: CheckFrequency;
  episode_order: EpisodeOrder;
  options: DownloadOptions;
  last_checked: number | null;
  done: number;
};

export type HistoryItem = {
  id: number;
  video_id: string;
  mode: Mode;
  season: number | null;
  episode: number | null;
  path: string;
  missing: number;
  created_at: number;
  options: DownloadOptions;
  title: string;
  channel: string | null;
  show: string | null;
};

export type Settings = {
  temp_dir: string;
  tv_dir: string;
  movie_dir: string;
  music_dir: string;
  plex_url: string;
  plex_token_set: boolean;
  plex_tv_section: string;
  plex_movie_section: string;
  plex_music_section: string;
  plex_path_from: string;
  plex_path_to: string;
  file_uid: number;
  file_gid: number;
  min_free_gb: number;
  ytdlp_auto_update: boolean;
  default_options: DownloadOptions;
};

export type Stats = {
  playlists: { total: number; tv: number; movie: number; audio: number; auto_checked: number };
  videos: { total: number; unique: number; tv: number; movie: number; audio: number; ads_removed: number; channels: number };
  storage: { total_bytes: number; tv_bytes: number; movie_bytes: number; audio_bytes: number; missing: number };
  disk: { free_bytes: number | null; total_bytes: number | null };
  runtime: { seconds: number; known: number; videos: number };
  queue: { queued: number; active: number; failed: number };
  top_channels: { channel: string; count: number }[];
  activity: number[];
  recent: { title: string; mode: Mode; created_at: number }[];
};

export type PlexLibrary ={ id: string; title: string; type: string; agent: string };
