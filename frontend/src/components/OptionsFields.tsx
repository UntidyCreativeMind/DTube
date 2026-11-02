import { AD_LEVELS, AdLevel, AUDIO_LANGUAGES, DownloadOptions, QUALITIES, Quality, SUBTITLE_LANGUAGES, SUBTITLE_MODES, SubtitleMode } from "../api";

type Props = {
  value: DownloadOptions;
  onChange: (changes: Partial<DownloadOptions>) => void;
  audioOnly?: boolean;
};

export default function OptionsFields({ value, onChange, audioOnly = false }: Props) {
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        {!audioOnly && (
          <label className="block space-y-1 text-sm">
            <span className="text-muted">Quality</span>
            <select className="field" value={value.quality} onChange={(e) => onChange({ quality: e.target.value as Quality })}>
              {QUALITIES.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
            </select>
          </label>
        )}
        <label className="block space-y-1 text-sm">
          <span className="text-muted">Audio language</span>
          <select className="field" value={value.audio_lang} onChange={(e) => onChange({ audio_lang: e.target.value })}>
            {AUDIO_LANGUAGES.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
          </select>
        </label>
        <label className="block space-y-1 text-sm">
          <span className="text-muted">Ad removal</span>
          <select className="field" value={value.ad_removal} onChange={(e) => onChange({ ad_removal: e.target.value as AdLevel })}>
            {AD_LEVELS.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
          </select>
          <span className="block text-xs text-muted">{AD_LEVELS.find((l) => l.id === value.ad_removal)!.hint}</span>
        </label>
        {!audioOnly && (
          <label className="block space-y-1 text-sm">
            <span className="text-muted">Subtitles</span>
            <select className="field" value={value.subtitles} onChange={(e) => onChange({ subtitles: e.target.value as SubtitleMode })}>
              {SUBTITLE_MODES.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
            </select>
          </label>
        )}
        {!audioOnly && value.subtitles !== "off" && (
          <label className="block space-y-1 text-sm">
            <span className="text-muted">Subtitle language</span>
            <select className="field" value={value.subtitle_lang} onChange={(e) => onChange({ subtitle_lang: e.target.value })}>
              {SUBTITLE_LANGUAGES.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
            </select>
          </label>
        )}
      </div>
      {!audioOnly && (
        <label className="flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" checked={value.chapters} onChange={(e) => onChange({ chapters: e.target.checked })} />
          Embed chapters
        </label>
      )}
    </div>
  );
}
