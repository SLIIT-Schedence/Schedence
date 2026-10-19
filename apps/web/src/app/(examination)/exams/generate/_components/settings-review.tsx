import { Card } from "@/components/console/ui";
import { HARD_RULES } from "@/lib/examination-presentation";
import type { CycleReview } from "@/lib/examination-workspace-types";

export function HardRuleDescriptions() {
  return <div className="space-y-2">{HARD_RULES.map(([code, title, description]) => <details key={code} id={`rule-${code}`} className="scroll-mt-24 rounded-lg border border-line p-3"><summary className="cursor-pointer text-sm font-semibold text-ink">{code} · {title}</summary><p className="mt-2 text-sm leading-6 text-muted">{description}</p></details>)}</div>;
}

function SettingValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) return <>{value.join(", ") || "None"}</>;
  if (value !== null && typeof value === "object") return <dl className="space-y-1">{Object.entries(value).map(([key, item]) => <div key={key}><dt className="inline font-medium">{key}: </dt><dd className="inline"><SettingValue value={item} /></dd></div>)}</dl>;
  return <>{value === null ? "No limit / not set" : typeof value === "boolean" ? value ? "Enabled" : "Disabled" : String(value)}</>;
}

export function SettingsReview({ review }: { review: CycleReview }) {
  const scheduling = review.scheduling;
  const fairness = scheduling.settings.fairness;
  return <div className="space-y-5">
    <Card title="Current scheduling settings" description={`Source: ${scheduling.source} · version ${scheduling.version} · ${scheduling.status}`}>
      <div className="space-y-5 p-5">
        {scheduling.status === "invalid" && <div role="alert" className="rounded-xl bg-danger-soft p-4 text-sm text-danger">Generation is blocked until the saved settings are repaired. {scheduling.problems.map((item) => `${item.rule}: ${item.message}`).join(" ")}</div>}
        {Object.entries(scheduling.settings).map(([section, entries]) => <section key={section}>
          <h3 className="text-sm font-semibold capitalize text-ink">{section === "optimization" ? "Workload optimization — S1–S5 weights and thresholds" : section}</h3>
          <dl className="mt-2 grid gap-x-6 gap-y-2 rounded-xl bg-background p-4 text-xs sm:grid-cols-2">{entries && typeof entries === "object" && !Array.isArray(entries) ? Object.entries(entries).map(([key, value]) => <div key={key}><dt className="font-medium text-secondary-ink">{key.replaceAll("_", " ")}</dt><dd className="mt-1 break-words text-ink"><SettingValue value={value} /></dd></div>) : <div><dt>Stored value</dt><dd><SettingValue value={entries} /></dd></div>}</dl>
        </section>)}
        {fairness?.enabled === false && <p className="rounded-xl bg-secondary-soft p-4 text-sm leading-6 text-secondary-ink">Fairness protection is disabled in the current settings. Workload optimization may improve the total score while worsening some candidates&apos; scores; individual fairness limits are not enforced.</p>}
      </div>
    </Card>
    <Card title="Current seating rules" description={`Ruleset version ${review.seating.ruleset_version}. Date-specific venue blocks are checked by the generator.`}>
      <div className="space-y-3 p-5 text-sm text-muted">
        <p>Default examination seating: <strong className="text-ink">{review.seating.default.percentage}%</strong> · {review.seating.venue_count} venues · {review.seating.total_exam_capacity} effective exam seats / {review.seating.total_normal_capacity} full capacity.</p>
        <p>Unavailable venues: {review.seating.unavailable_venues.join(", ") || "None"}</p>
        {review.seating.venue_overrides.length ? <ul className="grid gap-2 sm:grid-cols-2">{review.seating.venue_overrides.map((item) => <li key={item.venue_id}>{item.venue_id}: {item.percentage}% examination seating</li>)}</ul> : <p>No venue-specific seating overrides.</p>}
      </div>
    </Card>
    <Card title="Hard constraints H1–H11" description="These mandatory rules cannot be relaxed by changing workload weights."><div className="space-y-4 p-5"><p className="text-sm text-muted">Backend mandatory rules: {scheduling.mandatory_rules.join(", ")}</p><HardRuleDescriptions /></div></Card>
  </div>;
}
