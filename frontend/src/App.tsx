import { Download, History, ListVideo, LogOut, Settings as SettingsIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "./api";
import AuthPage from "./pages/AuthPage";
import DownloadsPage from "./pages/DownloadsPage";
import HistoryPage from "./pages/HistoryPage";
import HomePage from "./pages/HomePage";
import PlaylistsPage from "./pages/PlaylistsPage";
import SettingsPage from "./pages/SettingsPage";

const TABS = [
  { id: "downloads", label: "Downloads", icon: Download, page: DownloadsPage },
  { id: "playlists", label: "Playlists", icon: ListVideo, page: PlaylistsPage },
  { id: "history", label: "History", icon: History, page: HistoryPage },
  { id: "settings", label: "Settings", icon: SettingsIcon, page: SettingsPage },
];

export default function App() {
  const [auth, setAuth] = useState<{ needs_setup: boolean; logged_in: boolean } | null>(null);
  const [tab, setTab] = useState("home");

  const refresh = () => api("/auth/status").then(setAuth);
  useEffect(() => {
    refresh();
  }, []);

  if (!auth) return null;
  if (!auth.logged_in) return <AuthPage setup={auth.needs_setup} onDone={refresh} />;

  const Page = TABS.find((t) => t.id === tab)?.page;
  const logout = () => api("/auth/logout", "POST").finally(refresh);

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-line bg-page/90 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center gap-2 px-4 py-3">
          <button onClick={() => setTab("home")} aria-label="DTube home"
            className="mr-2 cursor-pointer text-lg font-bold tracking-tight sm:mr-6">
            D<span className="text-accent">Tube</span>
          </button>
          <nav className="flex flex-1 gap-1 overflow-x-auto">
            {TABS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                aria-label={label}
                className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium ${
                  tab === id ? "bg-raised text-white" : "text-muted hover:text-white"
                }`}
              >
                <Icon size={16} />
                <span className="hidden sm:inline">{label}</span>
              </button>
            ))}
          </nav>
          <button onClick={logout} aria-label="Log out" title="Log out" className="p-2 text-muted hover:text-white">
            <LogOut size={18} />
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        {Page ? <Page /> : <HomePage onNavigate={setTab} />}
      </main>
    </div>
  );
}
