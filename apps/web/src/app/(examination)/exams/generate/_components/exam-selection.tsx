"use client";

import { Badge, Card, formatDate } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import {
  academicYears, countFor, EXAM_KINDS, type ExamChoice, type ExamOption, isGeneratable, matchExaminations, updateChoice,
} from "@/lib/examination-selection";
import { fieldClass } from "./period-calendar";

const number = new Intl.NumberFormat("en-GB");
const SEMESTERS = [1, 2] as const;
const choiceCard = "flex cursor-pointer items-start gap-3 rounded-xl border border-line bg-surface p-4 transition-colors hover:border-secondary/70 "
  + "has-[:checked]:border-primary has-[:checked]:bg-primary-soft has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-primary "
  + "has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60";

/** Step 1: academic year, examination type and semester, matched to real backend periods. */
export function ExamSelection({ options, choice, onChange, disabled }: {
  options: ExamOption[];
  choice: ExamChoice;
  onChange: (next: ExamChoice) => void;
  disabled: boolean;
}) {
  const years = academicYears(options);
  const matches = matchExaminations(options, choice);
  const selected = matches.find((option) => option.period_code === choice.periodCode);
  const change = (next: Omit<ExamChoice, "periodCode">) => onChange(updateChoice(options, next));

  return (
    <Card title="1. Select Examination" description="Choose the academic year, examination type and semester. Only examinations configured in the Examination Service are listed.">
      <div className="space-y-6 p-5">
        <div className="grid gap-5 md:grid-cols-[minmax(0,16rem)_minmax(0,1fr)]">
          <label htmlFor="academic-year" className="block text-sm font-semibold text-ink">Academic year
            <select id="academic-year" value={choice.year} disabled={disabled} className={fieldClass}
              onChange={(event) => change({ year: event.target.value, kind: choice.kind, semester: choice.semester })}>
              {years.map((year) => <option key={year} value={year}>{year}</option>)}
            </select>
          </label>
          <fieldset disabled={disabled}>
            <legend className="text-sm font-semibold text-ink">Examination type</legend>
            <div className="mt-1 grid gap-2 sm:grid-cols-2">
              {EXAM_KINDS.map((kind) => {
                const count = countFor(options, choice.year, kind);
                return (
                  <label key={kind} className={choiceCard}>
                    <input type="radio" name="exam-kind" value={kind} checked={choice.kind === kind} className="mt-1 accent-primary"
                      onChange={() => change({ year: choice.year, kind, semester: choice.semester })} />
                    <span>
                      <span className="block text-sm font-semibold text-ink">{kind === "FINAL" ? "FINAL examination" : "MID examination"}</span>
                      <span className="mt-0.5 block text-xs text-muted">{count ? `${count} configured in ${choice.year}` : `None configured in ${choice.year}`}</span>
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        </div>

        <fieldset disabled={disabled}>
          <legend className="text-sm font-semibold text-ink">Semester</legend>
          <div className="mt-1 grid gap-3 sm:grid-cols-2">
            {SEMESTERS.map((semester) => {
              const count = countFor(options, choice.year, choice.kind, semester);
              return (
                <label key={semester} className={choiceCard}>
                  <input type="radio" name="semester" value={semester} checked={choice.semester === semester} className="sr-only"
                    onChange={() => change({ year: choice.year, kind: choice.kind, semester })} />
                  <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-secondary-soft text-secondary-ink">
                    <Icon name="school" size={20} filled />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-base font-semibold text-ink">Semester {semester}</span>
                    <span className="mt-0.5 block text-xs text-muted">
                      {count ? `${count} ${choice.kind} examination${count > 1 ? "s" : ""} configured` : `No ${choice.kind} examination configured`}
                    </span>
                  </span>
                  {choice.semester === semester && <Icon name="check_circle" size={20} filled className="text-primary" />}
                </label>
              );
            })}
          </div>
        </fieldset>

        {choice.semester && matches.length > 1 && (
          <fieldset disabled={disabled}>
            <legend className="text-sm font-semibold text-ink">{matches.length} examinations match. Choose one</legend>
            <div className="mt-1 grid gap-2">
              {matches.map((option) => (
                <label key={option.period_code} className={choiceCard}>
                  <input type="radio" name="period" value={option.period_code} checked={choice.periodCode === option.period_code} className="mt-1 accent-primary"
                    onChange={() => onChange({ ...choice, periodCode: option.period_code })} />
                  <span>
                    <span className="block text-sm font-semibold text-ink">{option.period_name}</span>
                    <span className="mt-0.5 block text-xs text-muted">{formatDate(option.start_date)} – {formatDate(option.end_date)} · <span className="font-mono">{option.period_code}</span></span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        )}

        {choice.semester && !matches.length && (
          <div role="status" className="flex gap-3 rounded-xl bg-secondary-soft p-4 text-sm leading-6 text-secondary-ink">
            <Icon name="info" size={20} className="mt-0.5" />
            <div>
              <p className="font-semibold">Examination period not configured</p>
              <p>No {choice.kind} examination for Semester {choice.semester} in {choice.year} exists in the Examination Service. Choose another selection, or ask for the period and its module mapping to be configured.</p>
            </div>
          </div>
        )}

        {selected && <SelectedExamination option={selected} />}
        {!choice.semester && <p className="text-sm text-muted">Choose a semester to see the matching examination.</p>}
      </div>
    </Card>
  );
}

function SelectedExamination({ option }: { option: ExamOption }) {
  const facts: [string, string, string?][] = [
    ["Exam dates", `${formatDate(option.start_date)} – ${formatDate(option.end_date)}`],
    ["Exam days", `${number.format(option.exam_days)} allowed exam days`],
    ["Mapped modules", option.module_count === null ? "Not available" : number.format(option.module_count),
      option.module_count === null ? "The cycle mapping could not be read." : undefined],
    ["Registrations", option.registration_count === null ? "Not accurately available" : number.format(option.registration_count),
      option.registration_count === null ? "Shown only when every mapped module examines all its registrations." : undefined],
  ];
  return (
    <section aria-label="Selected examination" className="rounded-2xl border border-primary/25 bg-background p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wider text-secondary-ink">Selected examination</p>
          <h3 className="mt-1 text-lg font-semibold text-ink">{option.period_name}</h3>
          <p className="mt-0.5 text-xs text-muted">Academic year {option.academic_year_label} · Semester {option.semester} · Period code <span className="font-mono">{option.period_code}</span></p>
        </div>
        <Badge tone={isGeneratable(option) ? "primary" : "neutral"}>{option.exam_kind}</Badge>
      </div>
      <dl className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {facts.map(([label, value, hint]) => (
          <div key={label}>
            <dt className="text-xs font-medium text-muted">{label}</dt>
            <dd className="mt-1 text-sm font-semibold text-ink">{value}</dd>
            {hint && <dd className="mt-0.5 text-xs text-muted">{hint}</dd>}
          </div>
        ))}
      </dl>
      {!isGeneratable(option) && (
        <p role="status" className="mt-4 rounded-xl bg-secondary-soft p-3 text-sm text-secondary-ink">
          MID timetable generation is not available in this wizard yet. Choose a FINAL examination to continue.
        </p>
      )}
      {isGeneratable(option) && option.module_count === 0 && (
        <p role="status" className="mt-4 rounded-xl bg-secondary-soft p-3 text-sm text-secondary-ink">
          No modules are mapped to this examination yet. You can review it, but generation needs a configured module mapping.
        </p>
      )}
    </section>
  );
}
