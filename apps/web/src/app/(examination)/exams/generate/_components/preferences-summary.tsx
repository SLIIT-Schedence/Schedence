import { Badge, Card } from "@/components/console/ui";
import { publicPreferences, settingLabel, type PreferencesSnapshot, type SettingsForm } from "@/lib/examination-settings";

/** A display-only summary: never changes the form, its defaults or its request values. */
export function PreferenceOverview({ form, status, description }: { form: SettingsForm; status: string; description: string }) {
  const minutes = (raw: string) => {
    const value = raw.trim() === "" ? NaN : Number(raw);
    if (!Number.isFinite(value) || value < 0) return "Check value";
    return value > 0 && value % 1440 === 0 ? `${value / 1440} ${value === 1440 ? "day" : "days"}`
      : value > 0 && value % 60 === 0 ? `${value / 60} ${value === 60 ? "hour" : "hours"}` : `${value} minutes`;
  };
  return <section aria-label="Selected scheduling settings" className="overflow-hidden rounded-2xl border border-primary/20 bg-primary-soft/40">
    <div className="flex flex-wrap items-start justify-between gap-3 px-5 pt-5">
      <div><h2 className="text-base font-semibold text-ink">Recommended Settings</h2><p className="mt-1 max-w-2xl text-sm leading-6 text-muted">{description}</p></div>
      <Badge tone="primary">{status}</Badge>
    </div>
    <dl className="grid gap-5 p-5 sm:grid-cols-3 sm:gap-6">
      <div><dt className="text-xs font-semibold uppercase tracking-wide text-primary-strong">Exam Rules</dt>
        <dd className="mt-2 text-sm font-semibold text-ink">{form.permitted_start_times.join(" · ") || "Add a start time"}</dd>
        <dd className="mt-1 text-xs leading-5 text-muted">{minutes(form.minimum_break_minutes)} minimum break · {form.seating_percentage}% seating</dd>
        <dd className="text-xs leading-5 text-muted">{form.max_rooms_per_exam || "No limit on"} rooms · Up to {form.max_sessions_per_exam} sittings per exam</dd>
      </div>
      <div><dt className="text-xs font-semibold uppercase tracking-wide text-primary-strong">Student Workload</dt>
        <dd className="mt-2 text-sm font-semibold text-ink">Aim for {minutes(form.preparation_target_minutes)} to prepare</dd>
        <dd className="mt-1 text-xs leading-5 text-muted">{minutes(form.difficult_target_minutes)} between difficult exams</dd>
        <dd className="text-xs leading-5 text-muted">Prefer up to {form.preferred_window_exams} exams over {form.window_days} days</dd>
      </div>
      <div><dt className="text-xs font-semibold uppercase tracking-wide text-primary-strong">Fairness</dt>
        <dd className="mt-2 text-sm font-semibold text-ink">Protection {form.fairness_enabled ? "on" : "off"}</dd>
        <dd className="mt-1 text-xs leading-5 text-muted">{form.fairness_enabled ? "Limit how much harder any student's schedule can become." : "Individual fairness limits are not enforced."}</dd>
        <dd className="text-xs leading-5 text-muted">Preparation targets and fairness apply in Optimized mode.</dd>
      </div>
    </dl>
  </section>;
}

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
