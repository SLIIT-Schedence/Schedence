import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { redirect } from "next/navigation";
import { type ReactNode, Suspense } from "react";
import { Badge, Card, EmptyState, first, LoadError, PageHeader, query, TableSkeleton } from "@/components/console/ui";
import { Icon, type IconName } from "@/components/icon";
import { getAcademicContext } from "@/lib/academic";
import { TEACHING_SERVICE } from "@/lib/teaching";
import {
  type Check,
  checkReadiness,
  getGenerationJob,
  getGenerationInputs,
  parseScope,
  type Readiness,
  type Scope,
  scopeLabel,
  scopeQuery,
  type Step,
  STEPS,
  TIME_LIMITS,
} from "@/lib/teaching-generation";
import { parseRulesSelection, parseSchedulingRules, serializeRules } from "@/lib/teaching-scheduling-rules";
import { GenerateForm } from "./_components/generate-form";
import { JobRefresher } from "./_components/job-refresher";
import { SchedulingRulesForm } from "./_components/scheduling-rules-form";
import { WizardLink } from "./_components/wizard-link";

export const metadata: Metadata = { title: "Generate timetable" };

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

const STEP_INFO: Record<Step, { label: string; icon: IconName; hint: string }> = {
  scope: { label: "Select scope", icon: "calendar_month", hint: "Choose the semester, academic year and stream to schedule." },
  inputs: { label: "Academic inputs", icon: "school", hint: "The institution's records that this timetable will be built from." },
  rules: { label: "Scheduling Rules", icon: "edit_note", hint: "Choose supported preferences and validate them against your institution's inputs." },
  readiness: { label: "Check readiness", icon: "fact_check", hint: "Every required input is checked before anything is solved." },
  generate: { label: "Generate", icon: "event_repeat", hint: "Start the solver, follow the job and open the validated draft." },
};

const SELECT =
  "h-10 w-full rounded-xl border border-line bg-surface px-3 text-sm text-ink transition-colors hover:border-secondary/70 focus:border-primary";
const LABEL = "block text-[13px] font-semibold text-muted";
const PRIMARY =
  "inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong";
const SECONDARY =
  "inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-line bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:bg-background";

const stepHref = (step: Step, scope: Scope, extra: Record<string, string | undefined> = {}) =>
  `/academic/timetables/generate${query({ step, ...scopeQuery(scope), ...extra })}`;

export default function GenerateTimetablePage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <div className="space-y-6">
      <Link href="/academic/timetables" className="inline-flex items-center gap-1.5 rounded-lg text-sm font-semibold text-muted transition-colors hover:text-primary-strong">
        <Icon name="arrow_back" size={18} />
        Teaching timetables
      </Link>
      <PageHeader
        title="Generate timetable"
        description="Build a new draft teaching timetable for your institution in five steps. Nothing is solved until you click Generate."
      />
      <Suspense fallback={<Card><TableSkeleton rows={5} /></Card>}>
        <Wizard searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Wizard({ searchParams }: { searchParams: SearchParams }) {
  const context = await getAcademicContext();
  if (!context) redirect("/");
  const raw = await searchParams;
  const value = (key: string) => first(raw[key])?.slice(0, 100);
  const requested = value("step");
  const step: Step = STEPS.includes(requested as Step) ? (requested as Step) : "scope";
  const scope = parseScope({ semester: value("semester"), year: value("year"), stream: value("stream") });
  const job = value("job");
  const rulesSelection = first(raw.rules);
  const institution = context.primary;

  return (
    <>
      <Stepper current={step} scope={scope} />
      <Card
        title={`${STEPS.indexOf(step) + 1}. ${STEP_INFO[step].label}`}
        description={STEP_INFO[step].hint}
        actions={step !== "scope" ? <Badge tone="neutral">{scopeLabel(scope)}</Badge> : undefined}
      >
        <Suspense
          fallback={
            <div aria-busy="true">
              {step !== "scope" && (
                <p className="px-5 pt-5 text-[13px] text-muted">
                  Preparing and validating the institution&apos;s inputs against its own records. This can take up to a minute.
                </p>
              )}
              <TableSkeleton rows={5} />
            </div>
          }
        >
          {step === "scope" && <ScopeStep scope={scope} rulesSelection={rulesSelection} />}
          {step === "inputs" && <InputsStep scope={scope} institutionId={institution.id} institution={institution.name} rulesSelection={rulesSelection} />}
          {step === "rules" && <RulesStep scope={scope} institutionId={institution.id} rulesSelection={rulesSelection} />}
          {step === "readiness" && <ReadinessStep scope={scope} institutionId={institution.id} rulesSelection={rulesSelection} />}
          {step === "generate" && <GenerateStep scope={scope} institutionId={institution.id} institution={institution.name} job={job} rulesSelection={rulesSelection} />}
        </Suspense>
      </Card>
    </>
  );
}

// ---------------------------------------------------------------------------------------------- stepper

function Stepper({ current, scope }: { current: Step; scope: Scope }) {
  const index = STEPS.indexOf(current);
  return (
    <nav aria-label="Generation steps" className="rounded-2xl border border-line bg-surface px-4 py-4 sm:px-6">
      <p className="mb-3 text-[13px] font-semibold text-muted lg:hidden">
        Step {index + 1} of {STEPS.length} · {STEP_INFO[current].label}
      </p>
      <ol className="flex flex-wrap items-center gap-y-3">
        {STEPS.map((step, position) => {
          const done = position < index;
          const active = position === index;
          const marker = (
            <span
              className={`grid size-9 shrink-0 place-items-center rounded-full border-2 text-sm font-semibold transition-colors ${
                done ? "border-primary bg-primary text-white" : active ? "border-primary bg-surface text-primary-strong" : "border-line bg-surface text-muted"
              }`}
            >
              {done ? <Icon name="check_circle" size={18} filled /> : <Icon name={STEP_INFO[step].icon} size={18} />}
            </span>
          );
          const label = (
            <span className={`hidden text-sm font-semibold lg:inline ${active ? "text-ink" : done ? "text-primary-strong" : "text-muted"}`}>
              {STEP_INFO[step].label}
            </span>
          );
          return (
            <li key={step} className="flex flex-1 items-center gap-2 last:flex-none" aria-current={active ? "step" : undefined}>
              {done ? (
                <WizardLink href={stepHref(step, scope)} className="flex items-center gap-2 rounded-xl hover:opacity-80">
                  {marker}
                  {label}
                </WizardLink>
              ) : (
                <span className="flex items-center gap-2">
                  {marker}
                  {label}
                </span>
              )}
              {position < STEPS.length - 1 && <span aria-hidden="true" className={`mx-2 h-0.5 min-w-4 flex-1 rounded-full ${done ? "bg-primary" : "bg-line"}`} />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function Footer({ back, next }: { back?: ReactNode; next?: ReactNode }) {
  return <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-5 py-4">{back ?? <span />}{next}</div>;
}

const BackLink = ({ href, children = "Back" }: { href: string; children?: ReactNode }) => (
  <WizardLink href={href} className={SECONDARY}>
    <Icon name="arrow_back" size={18} />
    {children}
  </WizardLink>
);

const NextLink = ({ href, children = "Next" }: { href: string; children?: ReactNode }) => (
  <WizardLink href={href} className={PRIMARY}>
    {children}
    <Icon name="arrow_forward" size={18} />
  </WizardLink>
);

// -------------------------------------------------------------------------------------------- 1. scope

function ScopeStep({ scope, rulesSelection }: { scope: Scope; rulesSelection?: string }) {
  return (
    <Form action="/academic/timetables/generate">
      <input type="hidden" name="step" value="inputs" />
      {rulesSelection && <input type="hidden" name="rules" value={rulesSelection} />}
      <div className="grid gap-4 px-5 py-5 sm:grid-cols-3">
        <label className={LABEL}>
          Semester
          <select name="semester" defaultValue={scope.semester} className={`mt-1 ${SELECT}`}>
            <option value="1">Semester 1</option>
            <option value="2">Semester 2</option>
          </select>
        </label>
        <label className={LABEL}>
          Academic year
          <select name="year" defaultValue={scope.year ?? ""} className={`mt-1 ${SELECT}`}>
            <option value="">All years (1–4)</option>
            {[1, 2, 3, 4].map((year) => (
              <option key={year} value={year}>
                Year {year}
              </option>
            ))}
          </select>
        </label>
        <label className={LABEL}>
          Stream
          <select name="stream" defaultValue={scope.stream ?? ""} className={`mt-1 ${SELECT}`}>
            <option value="">Weekday and weekend</option>
            <option value="WEEKDAY">Weekday</option>
            <option value="WEEKEND">Weekend</option>
          </select>
        </label>
        <p className="text-[13px] leading-5 text-muted sm:col-span-3">
          Each semester is generated separately. Choosing all years and both streams schedules every applicable student group in
          the semester.
        </p>
      </div>
      <Footer
        back={<BackLink href="/academic/timetables">Cancel</BackLink>}
        next={
          <button type="submit" className={PRIMARY}>
            Next
            <Icon name="arrow_forward" size={18} />
          </button>
        }
      />
    </Form>
  );
}

// ------------------------------------------------------------------------------------------- 2. inputs

function InputTile({ icon, title, status, value, children }: { icon: IconName; title: string; status: "available" | "missing"; value: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-2xl border border-line p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-2 text-sm font-semibold text-ink">
          <Icon name={icon} size={18} className="text-secondary" />
          {title}
        </span>
        {status === "available" ? <Badge tone="accent">Loaded</Badge> : <Badge tone="neutral">Not available</Badge>}
      </div>
      <p className="text-xl font-semibold tracking-tight text-ink">{value}</p>
      <div className="text-[13px] leading-5 text-muted">{children}</div>
    </div>
  );
}

async function InputsStep({ scope, institutionId, institution, rulesSelection }: { scope: Scope; institutionId: string; institution: string; rulesSelection?: string }) {
  const readiness = await checkReadiness(institutionId, scope.semester, scope.year, scope.stream, rulesSelection);
  const { staff, inputs: assessed } = readiness;
  const data = assessed.ok ? assessed.data.inputs : null;
  const reason = !assessed.ok
    ? assessed.error.code === "READINESS_DISABLED"
      ? "Input readiness is not enabled in this Teaching Service deployment."
      : `The inputs could not be validated (${assessed.error.message}).`
    : "The inputs did not pass owner-record validation; see Check readiness.";
  const tile = (value: number | undefined, unit: string) => (data && value !== undefined ? `${value} ${unit}` : "Not available");
  const status = data ? "available" : "missing";
  return (
    <>
      <div className="grid gap-3 px-5 py-5 sm:grid-cols-2 xl:grid-cols-3">
        <InputTile icon="badge" title="Staff" status={status} value={tile(data?.staff, "staff")}>
          {data ? (staff ? staff.byRank.slice(0, 4).map(([rank, count]) => `${rank} ${count}`).join(" · ") : "Validated against the staff register.") : reason}
        </InputTile>
        <InputTile icon="menu_book" title="Modules" status={status} value={tile(data?.modules, "modules")}>
          {data ? `${data.module_offerings} module offerings and ${data.session_requirements} weekly session requirements in this scope.` : reason}
        </InputTile>
        <InputTile icon="group" title="Student groups" status={status} value={tile(data?.student_groups, "groups")}>
          {data ? `${data.specializations.map((name) => name.replaceAll("_", " ").toLowerCase()).join(", ") || "No tracks"}.` : reason}
        </InputTile>
        <InputTile icon="group" title="Teaching teams" status={status} value={tile(data?.teaching_team_entries, "entries")}>
          {data ? "Lecturer and instructor assignments to the modules in this scope." : reason}
        </InputTile>
        <InputTile icon="meeting_room" title="Venues" status={status} value={tile(data?.venues, "venues")}>
          {data ? "Campus-confirmed venue inventory with capacities and lab types." : reason}
        </InputTile>
        <InputTile icon="schedule" title="Lecturer preferences" status={status} value={tile(data?.lecturer_preferences, "preferences")}>
          {data ? "Working-day and weekend preferences from the lecturer preference records." : reason}
        </InputTile>
      </div>
      {data && (
        <p className="mx-5 mb-3 text-sm text-ink">
          <span className="font-semibold">{data.expected_sessions} sessions</span> to schedule for {scopeLabel(scope)}
          {data.blocked_scopes.length > 0 &&
            `; not scheduled (missing academic mapping): ${data.blocked_scopes.map((row) => `Year ${row.academic_year} ${row.specialization} ${row.stream.toLowerCase()}`).join(", ")}`}
          .
        </p>
      )}
      <p className="mx-5 mb-5 flex gap-2 rounded-xl bg-background px-3.5 py-3 text-[13px] leading-5 text-muted">
        <Icon name="info" size={18} className="mt-px shrink-0" />
        <span>
          Prepared for {institution} and checked field by field against its own Academic Data Service and Resource Service
          records. Nothing is stored and nothing is solved at this step. The Historical Experimental Preview is never used as an
          input.
        </span>
      </p>
      <Footer back={<BackLink href={stepHref("scope", scope)} />} next={<NextLink href={stepHref("rules", scope)} />} />
    </>
  );
}

// -------------------------------------------------------------------------------------------- 3. rules

async function RulesStep({ scope, institutionId, rulesSelection }: { scope: Scope; institutionId: string; rulesSelection?: string }) {
  const result = await getGenerationInputs(institutionId, scope.semester, scope.year, scope.stream);
  const report = result.ok ? result.data.scheduling_rules : null;
  const defaults = parseSchedulingRules(report?.experimental_defaults);
  if (!report || !defaults.ok) return <>
    <EmptyState icon="edit_note" title="Scheduling Rules unavailable" action={<NextLink href={stepHref("rules", scope)}>Retry</NextLink>}>
      {!result.ok ? result.error.message : result.data.checks.filter((row) => row.status === "MISSING").map((row) => row.detail).join(" ") || "The Teaching Service did not return supported rule defaults. Try again when input readiness is available."}
    </EmptyState>
    <Footer back={<BackLink href={stepHref("inputs", scope)} />} />
  </>;
  const selected = parseRulesSelection(rulesSelection);
  return <SchedulingRulesForm key={`${institutionId}:${scope.semester}:${scope.year}:${scope.stream}`}
    defaults={defaults.rules} selected={selected.ok ? selected.rules : defaults.rules} report={report} scope={scope}
    initialError={rulesSelection && !selected.ok ? selected.error : undefined} />;
}

// ---------------------------------------------------------------------------------------- 4. readiness

const STATUS: Record<Check["status"], { label: string; tone: "accent" | "primary" | "danger"; icon: IconName }> = {
  READY: { label: "Ready", tone: "accent", icon: "check_circle" },
  WARNING: { label: "Warning", tone: "primary", icon: "info" },
  MISSING: { label: "Missing", tone: "danger", icon: "error" },
};

function ReadinessList({ readiness }: { readiness: Readiness }) {
  const counts = (status: Check["status"]) => readiness.checks.filter((row) => row.status === status).length;
  return (
    <>
      <div className="flex flex-wrap items-center gap-2 px-5 pt-5">
        <Badge tone="accent">{counts("READY")} ready</Badge>
        <Badge tone="primary">{counts("WARNING")} warnings</Badge>
        <Badge tone={counts("MISSING") ? "danger" : "neutral"}>{counts("MISSING")} missing</Badge>
      </div>
      <ul className="divide-y divide-line px-5 py-3">
        {readiness.checks.map((row) => (
          <li key={row.key} className="flex gap-3 py-3">
            <Icon name={STATUS[row.status].icon} size={20} filled className={`mt-0.5 shrink-0 ${row.status === "READY" ? "text-accent-ink" : row.status === "MISSING" ? "text-danger" : "text-primary-strong"}`} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold text-ink">{row.title}</p>
                <Badge tone={STATUS[row.status].tone}>{STATUS[row.status].label}</Badge>
              </div>
              <p className="mt-0.5 break-words text-[13px] leading-5 text-muted">{row.detail}</p>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

function RulesSummary({ readiness }: { readiness: Readiness }) {
  const report = readiness.inputs.ok ? readiness.inputs.data.scheduling_rules : null;
  if (!report?.selected) return null;
  const connected = report.connected;
  return <details className="mx-5 mb-5 rounded-xl border border-line p-4">
    <summary className="cursor-pointer text-sm font-semibold text-ink">Applied scheduling rules</summary>
    <dl className="mt-3 grid gap-3 text-[13px] sm:grid-cols-2">
      {[
        ["Staff workload fairness", connected.workload_fairness ? "Each staff member's own teaching target" : "Off"],
        ["Daily load balance", [connected.daily_load.includes("MAX_STUDENT_DAILY_MINUTES") ? "Students" : "", connected.daily_load.includes("MAX_STAFF_DAILY_MINUTES") ? "Staff" : ""].filter(Boolean).join(" and ") || "Off"],
        ["Stored lecturer preference scoring", connected.lecturer_preferences ? "Active" : "No active scoring criteria"],
        ["Consecutive teaching", connected.consecutive ? `${report.selected.consecutive?.maximum_preferred_continuous_minutes ? `Preferred continuous run: ${report.selected.consecutive.maximum_preferred_continuous_minutes} min` : "No preferred maximum"}${connected.idle_gaps ? "; reduce idle gaps" : ""}` : "Off"],
      ].map(([label, value]) => <div key={label}><dt className="text-muted">{label}</dt><dd className="mt-0.5 font-semibold text-ink">{value}</dd></div>)}
    </dl>
    <p className="mt-3 text-[13px] text-muted">Experimental settings validated for this scope. Hard availability, lunch and approved workload limits remain protected.</p>
  </details>;
}

async function ReadinessStep({ scope, institutionId, rulesSelection }: { scope: Scope; institutionId: string; rulesSelection?: string }) {
  const readiness = await checkReadiness(institutionId, scope.semester, scope.year, scope.stream, rulesSelection);
  return (
    <>
      <ReadinessList readiness={readiness} />
      <RulesSummary readiness={readiness} />
      <Footer
        back={<BackLink href={stepHref("rules", scope)} />}
        next={
          readiness.ready ? (
            <NextLink href={stepHref("generate", scope)} />
          ) : (
            <span className="flex items-center gap-3">
              <span className="text-[13px] text-danger">Resolve the missing items to continue.</span>
              <span aria-disabled="true" className={`${PRIMARY} cursor-not-allowed opacity-50`}>
                Next
                <Icon name="arrow_forward" size={18} />
              </span>
            </span>
          )
        }
      />
    </>
  );
}

// ----------------------------------------------------------------------------------------- 5. generate

async function GenerateStep({ scope, institutionId, institution, job, rulesSelection }: { scope: Scope; institutionId: string; institution: string; job?: string; rulesSelection?: string }) {
  if (job) return <JobPanel scope={scope} institutionId={institutionId} jobId={job} />;
  const readiness = await checkReadiness(institutionId, scope.semester, scope.year, scope.stream, rulesSelection);
  if (!readiness.ready || !readiness.body) {
    return (
      <>
        <EmptyState icon="block" title="Generation is blocked" action={<BackLink href={stepHref("readiness", scope)}>Review readiness</BackLink>}>
          {institution} does not pass every readiness check for {scopeLabel(scope)}, so the Generate button is not available.
          Nothing has been solved.
        </EmptyState>
      </>
    );
  }
  const body = readiness.body;
  return (
    <>
      <dl className="grid gap-4 px-5 py-5 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ["Scope", scopeLabel(scope)],
          ["Input snapshot", `Version ${readiness.inputSnapshot?.revision ?? readiness.snapshot?.version_number ?? "—"} · ${readiness.scopeSummary?.groups ?? 0} groups`],
          ["Calendar", body.calendar_label.replaceAll("_", " ").toLowerCase()],
          ["Time limit", `${TIME_LIMITS.max_time_seconds / 60} min solve · ${TIME_LIMITS.job_timeout_seconds / 60} min job`],
        ].map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-[13px] text-muted">{label}</dt>
            <dd className="break-words font-semibold text-ink">{value}</dd>
          </div>
        ))}
      </dl>
      <RulesSummary readiness={readiness} />
      <p className="mx-5 mb-5 flex gap-2 rounded-xl bg-background px-3.5 py-3 text-[13px] leading-5 text-muted">
        <Icon name="info" size={18} className="mt-px shrink-0" />
        <span>
          Clicking Generate starts the solver for this scope. The result is independently validated and saved only as an
          unpublished draft for {institution}; it never replaces the Historical Experimental Preview or a published timetable.
        </span>
      </p>
      <div className="border-t border-line px-5 py-4">
        <GenerateForm
          semester={String(scope.semester)}
          year={scope.year ? String(scope.year) : ""}
          stream={scope.stream ?? ""}
          rules={serializeRules(body.scheduling_rules)}
          back={<BackLink href={stepHref("readiness", scope)} />}
        />
      </div>
    </>
  );
}

async function JobPanel({ scope, institutionId, jobId }: { scope: Scope; institutionId: string; jobId: string }) {
  const result = await getGenerationJob(institutionId, jobId);
  if (!result.ok) {
    return (
      <>
        <LoadError error={result.error} what="the generation job" retryHref={stepHref("generate", scope, { job: jobId })} service={TEACHING_SERVICE} />
        <Footer back={<BackLink href={stepHref("readiness", scope)} />} />
      </>
    );
  }
  const job = result.data;
  const active = job.status === "PENDING" || job.status === "RUNNING";
  const draftHref = job.timetable_version_id ? `/academic/timetables${query({ semester: job.request.semester, version: job.timetable_version_id })}` : undefined;
  return (
    <>
      <JobRefresher active={active} />
      <div className="space-y-4 px-5 py-5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={job.status === "FAILED" ? "danger" : job.status === "COMPLETED" ? "accent" : "primary"}>{job.status.toLowerCase()}</Badge>
          <span className="text-[13px] text-muted">
            Job {job.job_id.slice(0, 8)} · {Math.round(job.elapsed_seconds)} s elapsed
          </span>
        </div>
        {active && (
          <div className="space-y-1.5">
            <div className="h-2 overflow-hidden rounded-full bg-background" role="progressbar" aria-label="Generation in progress" aria-busy="true">
              <div className={`h-full rounded-full bg-primary ${job.status === "RUNNING" ? "w-2/3 animate-pulse" : "w-1/6"}`} />
            </div>
            <p className="text-[13px] text-muted">
              {job.status === "PENDING" ? "Queued: waiting for the generation worker." : "Solving and validating. This page refreshes on its own."}
            </p>
          </div>
        )}
        <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Sessions scheduled", `${job.scheduled_occurrences} of ${job.expected_occurrences}`],
            ["Solver", job.solver_status ? job.solver_status.toLowerCase() : "Not finished"],
            ["Independent validation", job.validation_status === "NOT_ASSESSED" ? "Not assessed yet" : job.validation_status.toLowerCase()],
            ["Students not scheduled (blocked scopes)", String(job.blocked_student_count)],
          ].map(([label, value]) => (
            <div key={label} className="min-w-0">
              <dt className="text-[13px] text-muted">{label}</dt>
              <dd className="font-semibold text-ink">{value}</dd>
            </div>
          ))}
        </dl>
        {job.unresolved_requirement_codes.length > 0 && (
          <p className="text-[13px] text-muted">Unresolved requirements: {job.unresolved_requirement_codes.map((code) => code.replaceAll("_", " ").toLowerCase()).join(", ")}.</p>
        )}
        {job.error && (
          <p className="flex gap-2 rounded-xl bg-danger-soft px-3.5 py-3 text-[13px] leading-5 text-danger">
            <Icon name="error" size={18} filled className="mt-px shrink-0" />
            <span>
              {job.error.message}
              {job.error.retry_with_new_key ? " You can try again from Check readiness." : ""}
            </span>
          </p>
        )}
        {job.status === "COMPLETED" && !draftHref && (
          <p className="text-[13px] text-muted">
            The result was validated but not saved as a draft ({job.persistence.toLowerCase().replaceAll("_", " ")} persistence).
          </p>
        )}
      </div>
      <Footer
        back={<BackLink href={stepHref("readiness", scope)} />}
        next={
          draftHref ? (
            <NextLink href={draftHref}>Open the draft timetable</NextLink>
          ) : job.status === "FAILED" ? (
            <NextLink href={stepHref("generate", scope)}>Start again</NextLink>
          ) : undefined
        }
      />
    </>
  );
}
