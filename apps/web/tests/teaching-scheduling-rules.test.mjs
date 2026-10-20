import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as jsxRuntime from "react/jsx-runtime";

// Isolated contracts, UI interaction and server-action tests. All service calls are mocked;
// the harness rejects any generation request. No browser session, database or CP-SAT is used.
function load(source, mocks = {}) {
  const code = ts.transpileModule(readFileSync(path.join(import.meta.dirname, "..", source), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const testModule = { exports: {} };
  new Function("require", "module", "exports", code)((name) => {
    assert.ok(Object.hasOwn(mocks, name), `Unexpected dependency: ${name}`);
    return mocks[name];
  }, testModule, testModule.exports);
  return testModule.exports;
}
const rules = load("src/lib/teaching-scheduling-rules.ts");
// generation/scheduling_rules.py EXPERIMENTAL_DEFAULTS (not university approved).
const defaults = () => ({ lecturer_preferences: { morning_start_minute: 510, afternoon_start_minute: 750, afternoon_end_minute: 1110,
  preferred_day_part_weight: 1, not_preferred_day_part_weight: 1, preferred_free_day_weight: 1, weekend_pattern_weight: 1 },
  workload_fairness: true, balance_student_daily_load: true, balance_staff_daily_load: true,
  consecutive: { maximum_preferred_continuous_minutes: 240, minimize_idle_gaps: false, apply_to_lecturers: true, apply_to_student_groups: false, penalty_weight: 1 } });
const scope = { semester: 1, year: 1, stream: "WEEKDAY" };
const selected = () => ({ ...defaults(), balance_staff_daily_load: false,
  consecutive: { ...defaults().consecutive, maximum_preferred_continuous_minutes: null, minimize_idle_gaps: true, apply_to_student_groups: true, penalty_weight: 3 } });
const report = (selection = null) => ({ selected: selection, experimental_defaults: defaults(), reference: selection ? "EXPERIMENTAL:rules:fixture" : null,
  connected: { lecturer_preferences: true, day_part_settings: true, workload_fairness: true, daily_load: ["MAX_STUDENT_DAILY_MINUTES"], consecutive: true, idle_gaps: false, objective_order: [] },
  protected: { lunch_enforced: true, lunch_periods: [{ academic_year: 1, start_minute: 720, end_minute: 780 }], workload_policy_reference: "fixture",
    staff_with_weekly_cap: 1, staff_with_daily_cap: 1, not_available_handling: "HARD_UNAVAILABILITY" }, objective_order: ["DELIVERIES", "CONTACT_MINUTES", "CONSECUTIVE_PENALTY"] });
const ok = (data) => ({ ok: true, data });
const storedInput = () => ({ institution_id: "institution", snapshot_id: "isolated-source", revision: 1,
  semantic_sha256: "c".repeat(64), validated_snapshot_id: "isolated-validated", validated_rules_reference: "EXPERIMENTAL:rules:fixture",
  semester: 1, academic_years: [1], streams: ["WEEKDAY"], calendar_label: "PP1_EXPERIMENTAL_CALENDAR",
  academic_term_reference: "isolated-term", calendar_reference: "a".repeat(64), scheduling_policy_reference: "b".repeat(64), persistence: "ISOLATED_POSTGRESQL" });
const redirect = (href) => { throw Object.assign(new Error("redirect"), { href }); };
const icons = { Icon: () => null };

test("backend defaults and every supported selection survive form and URL round trips", () => {
  for (const value of [defaults(), selected(), rules.parseSchedulingRules({}).rules]) {
    const form = rules.rulesForm(value, defaults());
    const parsed = rules.rulesFromForm(form);
    assert.deepEqual(parsed, { ok: true, rules: value });
    for (const step of ["scope", "inputs", "rules", "readiness", "generate"]) {
      const url = new URL(rules.wizardHref(step, scope, rules.serializeRules(parsed.rules)), "http://web.test");
      assert.equal(url.searchParams.get("step"), step);
      assert.deepEqual(rules.parseRulesSelection(url.searchParams.get("rules")), parsed);
    }
  }
  assert.equal(rules.minuteLabel(1440), "24:00");
});

for (const [label, value] of [
  ["coerced boolean", { workload_fairness: "false" }], ["numeric boolean", { balance_staff_daily_load: 1 }],
  ["unknown rule", { automatic_relaxation: true }], ["empty consecutive", { consecutive: {} }],
  ["null switch", { consecutive: { minimize_idle_gaps: null, maximum_preferred_continuous_minutes: 240 } }],
  ["null weight", { consecutive: { minimize_idle_gaps: true, penalty_weight: null } }],
  ["fractional maximum", { consecutive: { maximum_preferred_continuous_minutes: 60.5 } }],
  ["off-grid maximum", { consecutive: { maximum_preferred_continuous_minutes: 61 } }],
  ["missing target", { consecutive: { minimize_idle_gaps: true, apply_to_lecturers: false } }],
  ["unbounded weight", { consecutive: { minimize_idle_gaps: true, penalty_weight: 11 } }],
  ["reversed windows", { lecturer_preferences: { ...defaults().lecturer_preferences, morning_start_minute: 780 } }],
  ["off-grid window", { lecturer_preferences: { ...defaults().lecturer_preferences, morning_start_minute: 511 } }],
  ["negative preference weight", { lecturer_preferences: { ...defaults().lecturer_preferences, preferred_free_day_weight: -1 } }],
  ["extra preference option", { lecturer_preferences: { ...defaults().lecturer_preferences, forms_enabled: true } }],
]) test(`rejects ${label} before any backend call`, () => assert.equal(rules.parseSchedulingRules(value).ok, false));

test("malformed, missing and oversized URL selections are never replaced silently", () => {
  for (const value of [undefined, "", "{bad", "null", "[]", "x".repeat(2401)]) assert.equal(rules.parseRulesSelection(value).ok, false);
  const form = rules.rulesForm(defaults(), defaults());
  for (const invalid of ["", "1.5", "-1", " ", "Infinity", "1e2"]) {
    assert.equal(rules.rulesFromForm({ ...form, penalty_weight: invalid }).ok, false);
  }
});

async function withGeneration(options, execute) {
  const calls = [], original = globalThis.fetch;
  const snapshot = { version_number: 2, version_id: "version", input_snapshot_id: "stored-snapshot", validation_status: "PASSED",
    calendar: { label: "PP1_TEST_CALENDAR", synthetic_data: true }, scopes: [{ semester: 1, academic_year: 1, stream: "WEEKDAY", specialization: "IT", scope_status: "SUPPORTED", student_groups: ["group"] }],
    policy_sources: [{ label: "PP1:calendar-configuration", sha256: "a".repeat(64) }, { label: "PP1:synthetic-elective-registration-evidence", sha256: "b".repeat(64) }],
    ...(options.sealed ? { institution_policy_revision: "v1" } : {}) };
  globalThis.fetch = async (url, init) => {
    assert.ok(url.startsWith("http://teaching.test/api/v1/teaching/generation-inputs?"), "test must never call a generation endpoint");
    calls.push({ url, ...init, body: init.body ? JSON.parse(init.body) : undefined });
    if (options.unavailable) return new Response(JSON.stringify({ error: { code: "READINESS_DISABLED", message: "Readiness is disabled." } }), { status: 503 });
    const selection = init.body ? JSON.parse(init.body) : null;
    return new Response(JSON.stringify({ institution_id: "institution", ready: !options.rejected,
      generation_enabled: options.worker !== false, inputs: options.prepared ? { student_groups: 30, specializations: ["IT"], blocked_scopes: [] } : null,
      checks: options.rejected ? [{ key: "scheduling-rules", status: "MISSING", detail: "No approved teaching targets." }] : [],
      scheduling_rules: options.noReport ? null : { ...report(options.mismatch ? defaults() : selection),
        ...(options.inactivePreferences ? { connected: { ...report(selection).connected, lecturer_preferences: false } } : {}) },
      stored_input: options.prepared ? { ...storedInput(), ...(options.wrongPreparedScope ? { academic_years: [4] } : {}) } : null,
      snapshot_stored: Boolean(options.prepared), university_approved: false }));
  };
  const generation = load("src/lib/teaching-generation.ts", {
    "server-only": {}, "next/navigation": { redirect }, react: { cache: (f) => f }, "@/auth": { auth: async () => ({ accessToken: "fixture-token" }) },
    "@/lib/academic": { allStaff: async () => ok({ total: 1, items: [{ academicRank: "Lecturer" }] }) },
    "@/lib/identity-api": { identityRequest: async () => ok({ institutions: [{ institution_id: "institution", permissions: options.forbidden ? [] : ["teaching:generate"] }] }) },
    "@/lib/service-api": { serviceUrl: () => "http://teaching.test", serviceRequest: () => assert.fail("unexpected service call") },
    "@/lib/teaching": { listTimetables: async () => ok({ items: options.noSnapshot ? [] : options.unsealedAlternative
      ? [{ ...snapshot, institution_policy_revision: "v1" }, { ...snapshot, institution_policy_revision: undefined, input_snapshot_id: "compatible-snapshot" }] : [snapshot] }),
      getWorkloadReport: async () => ok({ academic_term_reference: "fixture-term" }) },
    "@/lib/teaching-scheduling-rules": rules,
  });
  try { await execute({ generation, calls, snapshot }); } finally { globalThis.fetch = original; }
}

test("GET loads backend defaults; POST sends exact selected rules and scope with the existing token", async () => {
  await withGeneration({}, async ({ generation, calls }) => {
    const baseline = await generation.getGenerationInputs("institution", 1, 1, "WEEKDAY");
    assert.deepEqual(baseline.data.scheduling_rules.experimental_defaults, defaults());
    assert.equal(calls[0].method, "GET");
    const result = await generation.getGenerationInputs("institution", 1, 1, "WEEKDAY", rules.serializeRules(selected()));
    assert.ok(result.ok);
    assert.equal(calls[1].method, "POST");
    assert.deepEqual(calls[1].body, selected());
    assert.equal(calls[1].headers.Authorization, "Bearer fixture-token");
    const url = new URL(calls[1].url);
    assert.equal(url.searchParams.get("institution_id"), "institution");
    assert.equal(url.searchParams.get("academic_year"), "1");
    assert.equal(url.searchParams.get("stream"), "WEEKDAY");
    assert.equal(calls[1].cache, "no-store");
  });
});

test("invalid selections never reach fetch; missing or mismatched backend confirmation fails closed", async () => {
  await withGeneration({}, async ({ generation, calls }) => {
    assert.equal((await generation.getGenerationInputs("institution", 1, null, null, "bad")).ok, false);
    assert.equal(calls.length, 0);
  });
  for (const option of [{ noReport: true }, { mismatch: true }, { unavailable: true }]) await withGeneration(option, async ({ generation }) => {
    assert.equal((await generation.getGenerationInputs("institution", 1, null, null, rules.serializeRules(selected()))).ok, false);
  });
});

test("readiness builds the existing monolithic request with selected rules and original references", async () => {
  await withGeneration({}, async ({ generation, calls }) => {
    const result = await generation.checkReadiness("institution", 1, 1, "WEEKDAY", rules.serializeRules(selected()));
    assert.equal(result.ready, true);
    assert.deepEqual(result.body.scheduling_rules, selected());
    assert.equal(result.body.snapshot_id, "stored-snapshot");
    assert.equal(result.body.calendar_reference, "a".repeat(64));
    assert.equal(result.body.scheduling_policy_reference, "b".repeat(64));
    assert.equal(result.body.strategy, "MONOLITHIC");
    assert.deepEqual(result.body.academic_years, [1]);
    assert.deepEqual(result.body.streams, ["WEEKDAY"]);
    assert.equal(calls.length, 1);
  });
});

test("missing rules, denied permission, rejected rules, sealed policy, absent snapshot and disabled worker block generation", async () => {
  for (const options of [{}, { forbidden: true }, { rejected: true }, { sealed: true }, { noSnapshot: true }, { worker: false }]) {
    await withGeneration(options, async ({ generation }) => {
      const result = await generation.checkReadiness("institution", 1, 1, "WEEKDAY", Object.keys(options).length ? rules.serializeRules(selected()) : undefined);
      assert.equal(result.ready, false, JSON.stringify(options));
      assert.ok(result.checks.some((row) => row.status === "MISSING"));
    });
  }
});

test("a compatible stored snapshot is preferred over a sealed policy when both are available", async () => {
  await withGeneration({ unsealedAlternative: true }, async ({ generation }) => {
    const result = await generation.checkReadiness("institution", 1, 1, "WEEKDAY", rules.serializeRules(selected()));
    assert.equal(result.ready, true);
    assert.equal(result.body.snapshot_id, "compatible-snapshot");
  });
});

test("a validated isolated input version makes generation ready without creating timetable history", async () => {
  await withGeneration({ prepared: true, noSnapshot: true }, async ({ generation }) => {
    const result = await generation.checkReadiness("institution", 1, 1, "WEEKDAY", rules.serializeRules(selected()));
    assert.equal(result.ready, true);
    assert.equal(result.snapshot, null);
    assert.equal(result.inputSnapshot.revision, 1);
    assert.equal(result.body.snapshot_id, "isolated-source");
    assert.equal(result.body.academic_term_reference, "isolated-term");
    assert.equal(result.scopeSummary.groups, 30);
    assert.deepEqual(result.body.scheduling_rules, selected());
  });
});

test("isolated snapshot discovery rejects mismatched scopes and still requires an enabled worker", async () => {
  for (const options of [{ prepared: true, wrongPreparedScope: true }, { prepared: true, worker: false }]) {
    await withGeneration(options, async ({ generation }) => {
      const result = await generation.checkReadiness("institution", 1, 1, "WEEKDAY", rules.serializeRules(selected()));
      assert.equal(result.ready, false);
    });
  }
});

function actions(generation, submitted, context = { primary: { id: "institution" } }) {
  return load("src/lib/teaching-generation-actions.ts", { "next/navigation": { redirect },
    "@/lib/academic": { getAcademicContext: async () => context }, "@/lib/teaching-scheduling-rules": rules,
    "@/lib/teaching-generation": { ...generation, submitGeneration: async (body) => { submitted.push(body); return ok({ job_id: "fixture-job" }); } } });
}
function formFor(selection = selected(), intent = "validate") {
  const form = new FormData();
  for (const [key, value] of Object.entries({ ...scope, rules: rules.serializeRules(selection), intent })) form.set(key, String(value));
  return form;
}

test("Validate and Next only use read-only readiness; successful Next carries the exact selection", async () => {
  await withGeneration({}, async ({ generation, calls }) => {
    const submitted = [], action = actions(generation, submitted);
    const result = await action.reviewSchedulingRules({ status: "idle" }, formFor());
    assert.equal(result.status, "valid");
    assert.equal(result.rulesKey, rules.serializeRules(selected()));
    await assert.rejects(action.reviewSchedulingRules(result, formFor(selected(), "continue")), (error) => {
      const url = new URL(error.href, "http://web.test");
      return url.searchParams.get("step") === "readiness" && url.searchParams.get("rules") === rules.serializeRules(selected());
    });
    assert.equal(calls.length, 2);
    assert.equal(submitted.length, 0);
  });
});

test("Generate revalidates the hidden selection and passes it to preparation without trusting a client institution", async () => {
  await withGeneration({}, async ({ generation, calls }) => {
    const submitted = [], action = actions(generation, submitted), form = formFor();
    form.set("institution_id", "attacker");
    await assert.rejects(action.startGeneration({ status: "idle" }, form), (error) => new URL(error.href, "http://web.test").searchParams.get("job") === "fixture-job");
    assert.equal(calls.length, 1);
    assert.equal(submitted.length, 1); // Mock only: no /generations request is permitted by this harness.
    assert.equal(submitted[0].institution_id, "institution");
    assert.deepEqual(submitted[0].scheduling_rules, calls[0].body);
    assert.deepEqual(submitted[0].scheduling_rules, selected());
  });
});

test("stale or invalid forms never call submission; owner failures are visible", async () => {
  for (const option of [{ rejected: true }, { worker: false }, { unavailable: true }]) await withGeneration(option, async ({ generation }) => {
    const submitted = [], action = actions(generation, submitted);
    assert.equal((await action.startGeneration({ status: "idle" }, formFor())).status, "error");
    assert.equal(submitted.length, 0);
  });
  await withGeneration({}, async ({ generation, calls }) => {
    const submitted = [], action = actions(generation, submitted), form = formFor();
    form.set("rules", "bad");
    assert.equal((await action.startGeneration({ status: "idle" }, form)).status, "error");
    assert.equal((await action.reviewSchedulingRules({ status: "idle" }, form)).status, "error");
    assert.equal(calls.length, 0);
    assert.equal(submitted.length, 0);
  });
});

const nodes = (node) => node == null || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
function editor(initial = selected(), initialAction = { status: "idle" }) {
  const slots = []; let cursor = 0, location = "";
  const react = {
    useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial;
      return [slots[index], (value) => { slots[index] = value; }]; },
    useActionState() { return [initialAction, () => assert.fail("no action should run on render"), false]; },
  };
  const component = load("src/app/(academic)/academic/timetables/generate/_components/scheduling-rules-form.tsx", {
    "react/jsx-runtime": jsxRuntime, react, "@/components/icon": icons, "@/lib/teaching-scheduling-rules": rules,
    "@/lib/teaching-generation-actions": { reviewSchedulingRules: () => assert.fail("no API on render") },
  });
  const render = () => { cursor = 0; return component.SchedulingRulesForm({ defaults: defaults(), selected: initial, report: report(), scope }); };
  const all = () => nodes(render());
  return { all, render, location: () => location, markup: () => renderToStaticMarkup(createElement(() => render())),
    history: { replaceState: (_, __, href) => { location = href; } },
    find: (predicate) => { const found = all().find(predicate); assert.ok(found); return found; },
    control: (label) => nodes(all().find((node) => node.type === "label" && text(node).startsWith(label))).find((node) => node.type === "input" || node.type === "select"),
    button: (label) => all().find((node) => node.type === "button" && text(node) === label),
  };
}

test("editor renders loaded selections, protected lunch and visibly disabled unsupported settings", () => {
  const view = editor(), html = view.markup();
  assert.match(html, /Choose what to optimise/);
  assert.match(html, /Year 1 12:00–13:00/);
  assert.match(html, /Custom objective order/);
  assert.match(html, /Automatic rule relaxation/);
  assert.equal(view.all().filter((node) => node.type === "input" && node.props.disabled).length, 2);
  assert.equal(view.control("Balance staff days").props.checked, false);
  assert.equal(view.control("Reduce idle gaps").props.checked, true);
  assert.equal(view.control("Penalty weight").props.value, "3");
  assert.deepEqual(JSON.parse(view.find((node) => node.type === "input" && node.props.name === "rules").props.value), selected());
  assert.doesNotMatch(html, /Lecturer Preference Forms/);
});

test("edits persist, invalid values block Next, Reset restores backend defaults and invalidates old validation", () => {
  const view = editor(selected(), { status: "valid", rulesKey: rules.serializeRules(selected()), message: "Validated old selection" });
  const original = globalThis.window; globalThis.window = { history: view.history };
  try {
    view.control("Preferred maximum continuous").props.onChange({ target: { value: "180" } });
    assert.equal(JSON.parse(new URL(view.location(), "http://web.test").searchParams.get("rules")).consecutive.maximum_preferred_continuous_minutes, 180);
    assert.doesNotMatch(view.markup(), /Validated old selection/);
    view.control("Penalty weight").props.onChange({ target: { value: "11" } });
    assert.equal(view.button("Next").props.disabled, true);
    assert.match(view.markup(), /whole number from 1 to 10/);
    view.button("Reset to defaults").props.onClick();
    assert.equal(view.button("Next").props.disabled, false);
    assert.deepEqual(rules.parseRulesSelection(new URL(view.location(), "http://web.test").searchParams.get("rules")).rules, defaults());
    const reloaded = editor(JSON.parse(new URL(view.location(), "http://web.test").searchParams.get("rules")));
    assert.equal(reloaded.control("Penalty weight").props.value, "1");
  } finally { globalThis.window = original; }
});

test("Back and stepper links carry the latest edited rules, without leaking them to dashboard links", () => {
  const key = rules.serializeRules(selected());
  const { WizardLink } = load("src/app/(academic)/academic/timetables/generate/_components/wizard-link.tsx", {
    "react/jsx-runtime": jsxRuntime, "next/link": { default: () => null }, "next/navigation": { useSearchParams: () => new URLSearchParams({ rules: key }) },
  });
  const link = WizardLink({ href: rules.wizardHref("scope", scope), children: "Scope" });
  assert.equal(new URL(link.props.href, "http://web.test").searchParams.get("rules"), key);
  assert.equal(link.props.prefetch, false);
  assert.equal(WizardLink({ href: "/academic/timetables", children: "Cancel" }).props.href, "/academic/timetables");
});

async function page(step, generation, encoded = rules.serializeRules(selected())) {
  const pass = ({ children, ...props }) => createElement("div", props, children);
  const clientEditor = (props) => createElement("div", { "data-rules-editor": true, "data-selected": rules.serializeRules(props.selected), "data-defaults": rules.serializeRules(props.defaults) });
  const generateForm = (props) => createElement("div", { "data-generate-form": true, "data-rules": props.rules });
  const parameters = { step, semester: "1", year: "1", stream: "WEEKDAY", ...(encoded === undefined ? {} : { rules: encoded }) };
  const { default: Page } = load("src/app/(academic)/academic/timetables/generate/page.tsx", {
    "react/jsx-runtime": jsxRuntime, react: { Suspense: ({ children }) => children }, "next/navigation": { redirect },
    "next/form": { default: (props) => createElement("form", props) }, "next/link": { default: (props) => createElement("a", props) },
    "@/components/console/ui": { Badge: pass, Card: pass, EmptyState: ({ title, children, action }) => createElement("section", {}, title, children, action),
      first: (v) => Array.isArray(v) ? v[0] : v, LoadError: pass, PageHeader: pass, query: (p) => `?${new URLSearchParams(Object.entries(p).filter(([, v]) => v != null))}`, TableSkeleton: pass },
    "@/components/icon": icons, "@/lib/academic": { getAcademicContext: async () => ({ primary: { id: "institution", name: "Institution" } }) },
    "@/lib/teaching": { TEACHING_SERVICE: "Teaching" }, "@/lib/teaching-generation": generation, "@/lib/teaching-scheduling-rules": rules,
    "./_components/generate-form": { GenerateForm: generateForm }, "./_components/job-refresher": { JobRefresher: () => null },
    "./_components/scheduling-rules-form": { SchedulingRulesForm: clientEditor },
    "./_components/wizard-link": { WizardLink: (props) => createElement("a", props) },
  });
  async function expand(node) {
    if (node == null || typeof node !== "object") return node;
    if (Array.isArray(node)) return Promise.all(node.map(expand));
    if (typeof node.type === "function") return expand(await node.type(node.props));
    return { ...node, props: { ...node.props, children: await expand(node.props.children) } };
  }
  return expand(Page({ searchParams: Promise.resolve(parameters) }));
}

test("wizard loads backend defaults, orders Scheduling Rules before readiness and preserves scope selections", async () => {
  await withGeneration({}, async ({ generation, calls }) => {
    assert.deepEqual(generation.STEPS, ["scope", "inputs", "rules", "readiness", "generate"]);
    const tree = await page("rules", generation), all = nodes(tree);
    const editor = all.find((node) => node.props?.["data-rules-editor"]);
    assert.deepEqual(JSON.parse(editor.props["data-selected"]), selected());
    assert.deepEqual(JSON.parse(editor.props["data-defaults"]), defaults());
    assert.equal(calls[0].method, "GET");
    const scoped = nodes(await page("scope", generation));
    assert.equal(scoped.find((node) => node.type === "input" && node.props.name === "rules").props.value, rules.serializeRules(selected()));
    const inputs = nodes(await page("inputs", generation));
    assert.ok(inputs.some((node) => node.type === "a" && new URL(node.props.href, "http://web.test").searchParams.get("step") === "rules"));
  });
});

test("unavailable defaults show a retry and no editable form; Generate receives the reviewed selection", async () => {
  await withGeneration({ noReport: true }, async ({ generation }) => {
    const tree = await page("rules", generation);
    assert.match(text(tree), /Scheduling Rules unavailable/);
    assert.ok(!nodes(tree).some((node) => node.props?.["data-rules-editor"]));
  });
  await withGeneration({}, async ({ generation }) => {
    const tree = await page("generate", generation);
    const form = nodes(tree).find((node) => node.props?.["data-generate-form"]);
    assert.deepEqual(JSON.parse(form.props["data-rules"]), selected());
    const missing = await page("generate", generation, "");
    assert.match(text(missing), /Generation is blocked/);
    assert.ok(!nodes(missing).some((node) => node.props?.["data-generate-form"]));
  });
  await withGeneration({ inactivePreferences: true }, async ({ generation }) => {
    const tree = await page("readiness", generation);
    assert.match(text(tree), /Applied scheduling rules/);
    assert.match(text(tree), /No active scoring criteria/);
  });
});
