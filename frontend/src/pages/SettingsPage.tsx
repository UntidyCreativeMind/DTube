import { ChevronUp, Folder, FolderPlus, X } from "lucide-react";
import { ChangeEvent, ReactNode, useEffect, useState } from "react";
import { api, PlexLibrary, Settings } from "../api";
import OptionsFields from "../components/OptionsFields";

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="space-y-3 rounded-2xl border border-line bg-panel p-4 sm:p-5">
      <div>
        <h2 className="font-semibold">{title}</h2>
        {hint && <p className="mt-0.5 text-sm text-muted">{hint}</p>}
      </div>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="text-muted">{label}</span>
      {children}
    </label>
  );
}

function FolderPicker({ start, onPick, onClose }: { start: string; onPick: (path: string) => void; onClose: () => void }) {
  const [dir, setDir] = useState<{ path: string; parent: string | null; folders: string[] } | null>(null);
  const [error, setError] = useState("");

  const open = (path: string) =>
    api(`/fs?path=${encodeURIComponent(path)}`).then((d) => { setDir(d); setError(""); }).catch((e) => setError(e.message));

  useEffect(() => {
    open(start);
  }, []);

  const create = async () => {
    const name = prompt("New folder name");
    if (!name || !dir) return;
    try {
      const { path } = await api("/fs/mkdir", "POST", { path: dir.path, name });
      open(path);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/60 p-4">
      <div className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-2xl border border-line bg-panel">
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <p className="min-w-0 flex-1 truncate font-mono text-sm">{dir?.path}</p>
          <button onClick={onClose} aria-label="Close" className="text-muted hover:text-white"><X size={18} /></button>
        </div>
        <ul className="flex-1 overflow-y-auto p-2 text-sm">
          {dir?.parent && (
            <li>
              <button className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-muted hover:bg-raised" onClick={() => open(dir.parent!)}>
                <ChevronUp size={16} /> Up one level
              </button>
            </li>
          )}
          {dir?.folders.map((name) => (
            <li key={name}>
              <button className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-raised" onClick={() => open(`${dir.path}/${name}`)}>
                <Folder size={16} className="shrink-0 text-muted" /> <span className="truncate">{name}</span>
              </button>
            </li>
          ))}
        </ul>
        {error && <p className="px-4 pb-2 text-sm text-accent-strong">{error}</p>}
        <div className="flex justify-between gap-2 border-t border-line px-4 py-3">
          <button className="btn" onClick={create}><FolderPlus size={15} /> New folder</button>
          <button className="btn btn-primary" onClick={() => dir && onPick(dir.path)} disabled={!dir}>Use this folder</button>
        </div>
      </div>
    </div>
  );
}

const FOLDERS = [
  ["temp_dir", "Temporary downloads"],
  ["tv_dir", "TV shows library"],
  ["movie_dir", "Movies library"],
  ["music_dir", "Audio library"],
] as const;

export default function SettingsPage() {
  const [form, setForm] = useState<Settings | null>(null);
  const [token, setToken] = useState("");
  const [libraries, setLibraries] = useState<PlexLibrary[]>([]);
  const [picking, setPicking] = useState<(typeof FOLDERS)[number][0] | null>(null);
  const [status, setStatus] = useState("");
  const [plexStatus, setPlexStatus] = useState("");
  const [cookies, setCookies] = useState(false);
  const [cookieStatus, setCookieStatus] = useState("");
  const [system, setSystem] = useState<{ ytdlp: string; media_free_gb: number | null; media_total_gb: number | null } | null>(null);
  const [updating, setUpdating] = useState(false);
  const [password, setPassword] = useState("");
  const [passwordStatus, setPasswordStatus] = useState("");

  useEffect(() => {
    api<Settings>("/settings").then(setForm);
    api("/cookies").then((c) => setCookies(c.present));
    api("/system").then(setSystem);
  }, []);

  const connectPlex = async (url: string, plexToken: string) => {
    setPlexStatus("Connecting…");
    try {
      const found = await api<PlexLibrary[]>("/plex/libraries", "POST", { url, token: plexToken });
      setLibraries(found);
      setPlexStatus(`Connected. Found ${found.length} ${found.length === 1 ? "library" : "libraries"}.`);
    } catch (err) {
      setPlexStatus((err as Error).message);
    }
  };

  const hasPlex = !!form?.plex_token_set && !!form.plex_url;
  useEffect(() => {
    if (hasPlex) connectPlex("", "");
  }, [hasPlex]);

  if (!form) return null;

  const set = <K extends keyof Settings>(key: K, value: Settings[K]) => setForm({ ...form, [key]: value });
  const number = (key: keyof Settings) => (e: ChangeEvent<HTMLInputElement>) => set(key, Number(e.target.value) as never);

  const save = async () => {
    setStatus("Saving…");
    const { plex_token_set: _unused, ...values } = form;
    try {
      const { restarting, ...saved } = await api("/settings", "PUT", { ...values, plex_token: token });
      setForm(saved);
      setToken("");
      setStatus(restarting ? "Saved. DTube is restarting to apply the new file owner." : "Saved.");
    } catch (err) {
      setStatus((err as Error).message);
    }
  };

  const uploadCookies = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      await api("/cookies", "POST", { content: await file.text() });
      setCookies(true);
      setCookieStatus("Cookies saved.");
    } catch (err) {
      setCookieStatus((err as Error).message);
    }
  };

  const removeCookies = () => api("/cookies", "DELETE").then(() => { setCookies(false); setCookieStatus("Cookies removed."); });

  const updateYtdlp = async () => {
    setUpdating(true);
    try {
      const { ytdlp } = await api("/system/update-ytdlp", "POST");
      setSystem(system && { ...system, ytdlp });
    } catch (err) {
      setStatus((err as Error).message);
    }
    setUpdating(false);
  };

  const changePassword = async () => {
    try {
      await api("/auth/password", "POST", { password });
      setPassword("");
      setPasswordStatus("Password changed.");
    } catch (err) {
      setPasswordStatus((err as Error).message);
    }
  };

  const showLibraries = libraries.filter((l) => l.type === "show");
  const movieLibraries = libraries.filter((l) => l.type === "movie");
  const musicLibraries = libraries.filter((l) => l.type === "artist");

  return (
    <div className="space-y-4 pb-20">
      <h1 className="text-xl font-semibold">Settings</h1>

      <Section title="Folders" hint="Keep these on the same disk for instant moves.">
        {FOLDERS.map(([key, label]) => (
          <Field key={key} label={label}>
            <div className="flex gap-2">
              <input className="field font-mono text-sm" value={form[key]} readOnly placeholder="Not set" onClick={() => setPicking(key)} />
              <button className="btn shrink-0" type="button" onClick={() => setPicking(key)}>Browse</button>
            </div>
          </Field>
        ))}
        <div className="grid grid-cols-2 gap-3">
          <Field label="File owner user ID">
            <input className="field" type="number" min={0} value={form.file_uid} onChange={number("file_uid")} />
          </Field>
          <Field label="File owner group ID">
            <input className="field" type="number" min={0} value={form.file_gid} onChange={number("file_gid")} />
          </Field>
        </div>
        <p className="text-xs text-muted">Plex must be able to read files owned by this user. Changing it restarts DTube.</p>
      </Section>

      <Section title="Plex" hint="Use separate TV and movie libraries with the Plex NFO Series and Plex NFO Movie agents.">
        <Field label="Server address">
          <input className="field" placeholder="http://192.168.1.10:32400" value={form.plex_url} onChange={(e) => set("plex_url", e.target.value)} />
        </Field>
        <Field label={form.plex_token_set ? "Token (saved, enter a new one to replace it)" : "Token"}>
          <input className="field" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
        </Field>
        <div className="flex flex-wrap items-center gap-3">
          <button className="btn" type="button" onClick={() => connectPlex(form.plex_url, token)}>Test connection</button>
          <span className="text-sm text-muted">{plexStatus}</span>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="TV library">
            <select className="field" value={form.plex_tv_section} onChange={(e) => set("plex_tv_section", e.target.value)}>
              <option value="">Not set</option>
              {showLibraries.map((l) => <option key={l.id} value={l.id}>{l.title} ({l.agent})</option>)}
            </select>
          </Field>
          <Field label="Movie library">
            <select className="field" value={form.plex_movie_section} onChange={(e) => set("plex_movie_section", e.target.value)}>
              <option value="">Not set</option>
              {movieLibraries.map((l) => <option key={l.id} value={l.id}>{l.title} ({l.agent})</option>)}
            </select>
          </Field>
          <Field label="Music library (optional)">
            <select className="field" value={form.plex_music_section} onChange={(e) => set("plex_music_section", e.target.value)}>
              <option value="">Not set</option>
              {musicLibraries.map((l) => <option key={l.id} value={l.id}>{l.title} ({l.agent})</option>)}
            </select>
          </Field>
          <Field label="DTube path prefix (optional)">
            <input className="field" placeholder="/media" value={form.plex_path_from} onChange={(e) => set("plex_path_from", e.target.value)} />
          </Field>
          <Field label="Same folder as Plex sees it (optional)">
            <input className="field" placeholder="/data" value={form.plex_path_to} onChange={(e) => set("plex_path_to", e.target.value)} />
          </Field>
        </div>
      </Section>

      <Section title="Download defaults" hint="These are selected for you on each new download, and you can change them there.">
        <OptionsFields value={form.default_options}
          onChange={(changes) => set("default_options", { ...form.default_options, ...changes })} />
        <Field label="Keep free (GB)">
          <input className="field" type="number" min={0} value={form.min_free_gb} onChange={number("min_free_gb")} />
        </Field>
        <p className="text-xs text-muted">
          Max available saves as MKV, usually in VP9 or AV1, and files can be several times larger. Some Plex clients will
          transcode these. The other levels prefer H.264 MP4 for direct play.
        </p>
        <p className="text-xs text-muted">
          YouTube's own ad breaks are never in downloads. These levels cut segments creators build into the video, using
          the community-run SponsorBlock database, so very new videos may not have any segments marked yet.
        </p>
      </Section>

      <Section title="yt-dlp" hint={system ? `Version ${system.ytdlp}${system.media_free_gb !== null ? ` · ${system.media_free_gb} GB free of ${system.media_total_gb} GB` : ""}` : undefined}>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.ytdlp_auto_update} onChange={(e) => set("ytdlp_auto_update", e.target.checked)} />
          Update automatically once a day
        </label>
        <button className="btn" type="button" onClick={updateYtdlp} disabled={updating}>{updating ? "Updating…" : "Update now"}</button>
      </Section>

      <Section title="Cookies (optional)" hint="DTube works without these. Use a throwaway Google account, because logged-in downloading can get an account banned.">
        <div className="flex flex-wrap items-center gap-3">
          <label className="btn">
            {cookies ? "Replace cookies.txt" : "Upload cookies.txt"}
            <input type="file" accept=".txt" className="hidden" onChange={uploadCookies} />
          </label>
          {cookies && <button className="btn" type="button" onClick={removeCookies}>Remove</button>}
          <span className="text-sm text-muted">{cookieStatus || (cookies ? "Cookies are in use." : "No cookies set.")}</span>
        </div>
      </Section>

      <Section title="Password">
        <div className="flex flex-col gap-2 sm:flex-row">
          <input className="field" type="password" placeholder="New password (at least 10 characters)" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          <button className="btn shrink-0" type="button" onClick={changePassword} disabled={password.length < 10}>Change password</button>
        </div>
        {passwordStatus && <p className="text-sm text-muted">{passwordStatus}</p>}
      </Section>

      <div className="fixed inset-x-0 bottom-0 border-t border-line bg-page/90 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-end gap-3 px-4 py-3">
          <span className="min-w-0 truncate text-sm text-muted">{status}</span>
          <button className="btn btn-primary shrink-0" onClick={save}>Save settings</button>
        </div>
      </div>

      {picking && (
        <FolderPicker
          start={form[picking]}
          onClose={() => setPicking(null)}
          onPick={(path) => { set(picking, path); setPicking(null); }}
        />
      )}
    </div>
  );
}
