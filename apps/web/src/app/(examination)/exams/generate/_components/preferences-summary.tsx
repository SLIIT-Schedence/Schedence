import { Card } from "@/components/console/ui";
import { publicPreferences, settingLabel, type PreferencesSnapshot } from "@/lib/examination-settings";

export function PreferencesSummary({ snapshot }: { snapshot?: PreferencesSnapshot | null }) {
  const preferences = publicPreferences(snapshot);
  return <Card title="Effective scheduling preferences" description={preferences ? "Recorded for this timetable; generation and hard validation use these same values." : "Legacy timetable: uses the institution settings version recorded with the result."}>
    {preferences && <div className="space-y-4 p-5">
      <p className="text-sm text-muted">Default exam seating: {preferences.seating.default_percentage}%. Venue overrides: {Object.entries(preferences.seating.venue_overrides).map(([id, percentage]) => `${id}: ${percentage}%`).join(" · ") || "None"}.</p>
      <details><summary className="cursor-pointer text-sm font-semibold text-primary-strong">View applied preferences</summary>
        <dl className="mt-3 grid gap-3 sm:grid-cols-2">{Object.entries(preferences.settings).flatMap(([section, values]) => Object.entries(values).map(([key, value]) =>
          <div key={`${section}.${key}`}><dt className="text-xs text-muted">{settingLabel(`${section}.${key}`)}</dt><dd className="mt-1 text-sm font-medium text-ink">{Array.isArray(value) ? value.join(", ") : value === null ? "No limit" : typeof value === "boolean" ? value ? "Enabled" : "Disabled" : String(value)}</dd></div>))}</dl>
      </details>
      <p className="break-all font-mono text-xs text-muted">Preferences fingerprint: {preferences.fingerprint}</p>
    </div>}
  </Card>;
}
