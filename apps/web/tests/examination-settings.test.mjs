import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as jsxRuntime from "react/jsx-runtime";

// Step 4 "Scheduling preferences": validation, the saved document, server actions and the editor.
// Every Examination Service request is mocked: no real settings are read or changed.

function load(source, mocks) {
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

const lib = load("src/lib/examination-settings.ts", {});

// The document GET /settings/scheduling returns for the built-in defaults (SchedulingConfiguration().to_document()).
const defaults = () => ({
  scheduling: {
    permitted_start_times: ["08:30", "13:30"], minimum_break_minutes: 60, max_rooms_per_exam: null, max_sessions_per_exam: 3,
    venue_compatibility: { written: ["lecture_hall", "smart_classroom"], computer_based: ["computer_lab"], hybrid: ["lecture_hall", "smart_classroom", "computer_lab", "byod_lab"] },
    module_venue_overrides: { IT1010: ["computer_lab"] },
  },
  solver: { time_limit_seconds: 60.0, random_seed: 20261019, num_search_workers: 1 },
  optimization: { s1_weight: 25, s2_weight: 20, s3_weight: 30, s4_weight: 15, s5_weight: 10, short_gap_minutes: 720, preparation_target_minutes: 2880,
    difficult_target_minutes: 4320, high_difficulty_minimum: 4, window_days: 3, preferred_window_exams: 2, difficulty_weight: 50, credits_weight: 25,
    duration_weight: 25, reference_difficulty: 3, reference_credits: 4, reference_duration_minutes: 120, score_scale: 1000, load_scale: 1000, baseline_budget_percent: 10 },
  fairness: { enabled: false, max_individual_increase_points: 5.0, protect_worst_student: true, max_worst_increase_points: 0.0, automatic_relaxation: false },
});
const schedulingResponse = (changes = {}) => ({ institution_id: "inst", source: "saved", status: "valid", problems: [], version: 4, format: "scheduling-settings-v1",
  fingerprint: "f".repeat(64), reason: "Initial", updated_by: "user", settings: defaults(), mandatory_rules: Array.from({ length: 11 }, (_, i) => `H${i + 1}: rule`), history: [], ...changes });
const seatingResponse = (changes = {}) => ({ min_percentage: 1, max_percentage: 100, ruleset_version: 7,
  default: { scope: "default", venue_id: null, percentage: 50, version: 3, reason: "Initial", updated_by: null, updated_at: "2026-10-01T00:00:00Z" },
  venue_overrides: [{ scope: "venue", venue_id: "A304", percentage: 80, version: 2, reason: "Large lab", updated_by: null, updated_at: "2026-10-01T00:00:00Z" }],
  venue_count: 12, total_normal_capacity: 1200, total_exam_capacity: 640, unavailable_venues: [], history: [], ...changes });

const formFor = (scheduling = schedulingResponse(), seating = seatingResponse()) => lib.settingsForm(lib.schedulingReview(scheduling), lib.seatingReview(seating));

test("the form loads the real saved values and saving it unchanged reproduces the same complete document", () => {
  const form = formFor();
  assert.equal(form.minimum_break_minutes, "60");
  assert.equal(form.max_rooms_per_exam, "");
  assert.equal(form.time_limit_seconds, "60");
  assert.equal(form.seating_percentage, "50");
  assert.deepEqual(form.permitted_start_times, ["08:30", "13:30"]);
  assert.deepEqual(lib.settingsErrors(form), {});
  assert.deepEqual(lib.settingsDocument(defaults(), form), defaults());
  assert.deepEqual(lib.changedSettings(defaults(), lib.settingsDocument(defaults(), form)), []);
  // Shown defaults are the Examination Service's built-in values.
  for (const [key, rule] of Object.entries(lib.NUMBER_FIELDS)) assert.equal(rule.default, defaults()[rule.section][key], key);
});

test("only editable keys change; venue compatibility, module overrides, scales and automatic relaxation are kept", () => {
  const form = { ...formFor(), minimum_break_minutes: "90", max_rooms_per_exam: "4", s3_weight: "40", fairness_enabled: true, time_limit_seconds: "120.5",
    permitted_start_times: ["08:30", "13:30", "16:00"], score_scale: "1", automatic_relaxation: true, venue_compatibility: {} };
  const document = lib.settingsDocument(defaults(), form);
  assert.equal(document.scheduling.minimum_break_minutes, 90);
  assert.equal(document.scheduling.max_rooms_per_exam, 4);
  assert.equal(document.solver.time_limit_seconds, 120.5);
  assert.equal(document.fairness.enabled, true);
  for (const [section, key] of [["scheduling", "venue_compatibility"], ["scheduling", "module_venue_overrides"], ["optimization", "score_scale"], ["optimization", "load_scale"], ["fairness", "automatic_relaxation"]]) {
    assert.deepEqual(document[section][key], defaults()[section][key], key);
  }
  assert.deepEqual(lib.changedSettings(defaults(), document), ["fairness.enabled", "optimization.s3_weight", "scheduling.max_rooms_per_exam",
    "scheduling.minimum_break_minutes", "scheduling.permitted_start_times", "solver.time_limit_seconds"]);
  assert.equal(lib.settingsDocument(defaults(), { ...formFor(), max_rooms_per_exam: " " }).scheduling.max_rooms_per_exam, null);
});

test("the recommended solver profile changes only workers and the time limit, and only Optimized mode uses it", () => {
  const institution = formFor();
  assert.deepEqual([institution.num_search_workers, institution.time_limit_seconds], ["1", "60"]);
  const profile = lib.withRecommendedProfile(institution);
  assert.deepEqual(profile, { ...institution, num_search_workers: "4", time_limit_seconds: "120" });
  assert.deepEqual(lib.settingsErrors(profile), {}, "the profile passes the same validation as any other value");
  assert.deepEqual(institution, formFor(), "the institution form is not mutated");
  assert.deepEqual(lib.effectiveForm(profile, institution, "optimized"), profile);
  assert.deepEqual(lib.effectiveForm(profile, institution, "feasibility"), institution, "Feasibility falls back to the institution's solver values");
  const custom = { ...profile, num_search_workers: "8" };
  assert.deepEqual(lib.effectiveForm(custom, institution, "feasibility"), custom, "values the admin set are kept in every mode");
  assert.deepEqual(lib.effectiveForm(institution, institution, "optimized"), institution, "an explicit 1 worker / 60 s choice is not replaced");
  assert.equal(lib.solverSource(profile, institution), "recommended");
  assert.equal(lib.solverSource({ ...profile, time_limit_seconds: "120.0" }, institution), "recommended");
  assert.equal(lib.solverSource(institution, institution), "institution");
  assert.equal(lib.solverSource(custom, institution), "custom");
  // The payload passes the same checks the server action applies before it calls the Examination Service.
  const prefs = lib.schedulingPreferences(profile);
  assert.deepEqual([prefs.solver.num_search_workers, prefs.solver.time_limit_seconds], [4, 120]);
  assert.deepEqual(lib.parseSchedulingPreferences(JSON.parse(JSON.stringify(prefs))), prefs);
});

test("validation mirrors the backend limits with clear messages", () => {
  const errors = (changes) => lib.settingsErrors({ ...formFor(), ...changes });
  assert.match(errors({ minimum_break_minutes: "1441" }).minimum_break_minutes, /0 to 1440/);
  assert.match(errors({ minimum_break_minutes: "-5" }).minimum_break_minutes, /whole number/);
  assert.match(errors({ minimum_break_minutes: "1.5" }).minimum_break_minutes, /whole number/);
  assert.match(errors({ max_rooms_per_exam: "0" }).max_rooms_per_exam, /Leave empty for no limit/);
  assert.match(errors({ max_sessions_per_exam: "11" }).max_sessions_per_exam, /1 to 10/);
  assert.match(errors({ time_limit_seconds: "0" }).time_limit_seconds, /greater than 0 and no more than 300/);
  assert.match(errors({ time_limit_seconds: "301" }).time_limit_seconds, /300/);
  assert.equal(errors({ time_limit_seconds: "0.5" }).time_limit_seconds, undefined);
  assert.match(errors({ num_search_workers: "17" }).num_search_workers, /1 to 16/);
  assert.match(errors({ permitted_start_times: [] }).permitted_start_times, /at least one/);
  assert.match(errors({ permitted_start_times: ["8:30"] }).permitted_start_times, /08:30/);
  assert.match(errors({ permitted_start_times: ["08:30", "08:30"] }).permitted_start_times, /only once/);
  assert.match(errors({ short_gap_minutes: "3000" }).preparation_target_minutes, /less than the preparation target/);
  assert.match(errors({ high_difficulty_minimum: "6" }).high_difficulty_minimum, /1 to 5/);
  assert.match(errors({ baseline_budget_percent: "100" }).baseline_budget_percent, /1 to 99/);
  assert.match(errors({ difficulty_weight: "0", credits_weight: "0", duration_weight: "0" }).difficulty_weight, /At least one/);
  assert.match(errors({ max_individual_increase_points: "100.5" }).max_individual_increase_points, /0 to 100/);
  assert.equal(errors({ max_individual_increase_points: "2.5" }).max_individual_increase_points, undefined);
  assert.match(errors({ seating_percentage: "0" }).seating_percentage, /1 to 100/);
  assert.match(lib.reasonError("abc"), /5–500/);
  assert.match(lib.reasonError("Faculty\tboard"), /single line/);
  assert.equal(lib.reasonError("  Faculty board decision  "), undefined);
});

const redirect = (destination) => { throw Object.assign(new Error("Redirect"), { destination }); };

function actionHarness(options = {}) {
  const calls = [];
  let scheduling = options.scheduling ?? schedulingResponse();
  let seating = options.seating ?? seatingResponse();
  const actions = load("src/lib/examination-settings-actions.ts", {
    "@/lib/examination-settings": lib,
    "@/lib/examination-access": { requireExaminationAdmin: async () => { if (options.denied) redirect("/"); return { id: "admin" }; } },
    "@/lib/examination-api": { examinationRequest: async (url, init = {}) => {
      calls.push({ url, ...init });
      if (url === "/api/v1/settings/scheduling" && init.method === "PUT") {
        if (options.schedulingFails) return { ok: false, error: { status: 409, code: "HTTP_409", message: "The scheduling settings were changed by someone else." } };
        if (!init.query.dry_run) scheduling = { ...scheduling, source: "saved", status: "valid", version: scheduling.version + 1, fingerprint: "e".repeat(64), settings: init.body.settings };
        return { ok: true, data: { dry_run: init.query.dry_run, changed: true, changed_fields: lib.changedSettings(options.scheduling?.settings ?? defaults(), init.body.settings), version: scheduling.version } };
      }
      if (url === "/api/v1/settings/exam-seating/default") {
        if (options.seatingFails) return { ok: false, error: { status: 403, code: "HTTP_403", message: "Permission denied" } };
        if (!init.query.dry_run) seating = { ...seating, ruleset_version: seating.ruleset_version + 1, default: { ...seating.default, percentage: init.body.percentage, version: seating.default.version + 1 } };
        return { ok: true, data: { dry_run: init.query.dry_run, changed: true, old_percentage: 50, new_percentage: init.body.percentage, affected_venues: [{}, {}], newly_unavailable_venues: [] } };
      }
      if (url === "/api/v1/settings/scheduling") return { ok: true, data: scheduling };
      if (url === "/api/v1/settings/exam-seating") return { ok: true, data: seating };
      if (url === "/api/v1/timetables") return { ok: true, data: { total: init.query.status === "DRAFT" ? 3 : 1 } };
      throw new Error(`Unexpected request: ${url}`);
    } },
  });
  const expected = { scheduling_version: (options.scheduling ?? schedulingResponse()).version, seating_version: 3 };
  return { ...actions, calls, expected, puts: () => calls.filter((call) => call.method === "PUT") };
}

test("preview sends only dry-run changes with the loaded versions and counts stale timetables in every cycle", async () => {
  const h = actionHarness();
  const form = { ...formFor(), minimum_break_minutes: "90", seating_percentage: "60" };
  const result = await h.previewSettingsChange(form, h.expected, "  Faculty board decision ");
  assert.equal(result.ok, true);
  assert.deepEqual(result.data.scheduling_fields, ["scheduling.minimum_break_minutes"]);
  assert.deepEqual(result.data.seating, { old_percentage: 50, new_percentage: 60, affected_venues: 2, newly_unavailable_venues: [] });
  assert.equal(result.data.drafts, 3);
  assert.equal(result.data.approved, 1);
  assert.ok(h.puts().length === 2 && h.puts().every((call) => call.query.dry_run === true));
  const [settingsPut, seatingPut] = h.puts();
  assert.deepEqual(settingsPut.body, { settings: { ...defaults(), scheduling: { ...defaults().scheduling, minimum_break_minutes: 90 } }, reason: "Faculty board decision", expected_version: 4 });
  assert.equal(settingsPut.query.replace_invalid, undefined);
  assert.deepEqual(seatingPut.body, { percentage: 60, reason: "Faculty board decision", expected_version: 3 });
  // Timetable counts are institution-wide (no period filter); venue overrides are never requested to change.
  assert.ok(h.calls.filter((call) => call.url === "/api/v1/timetables").every((call) => !("period_code" in call.query)));
  assert.equal(h.calls.some((call) => call.url.includes("/venues/")), false);
});

test("an unchanged seating value is not sent, and no changes at all is refused without a request", async () => {
  const h = actionHarness();
  const preview = await h.previewSettingsChange({ ...formFor(), s1_weight: "30" }, h.expected, "Lower clustering");
  assert.equal(preview.ok, true);
  assert.deepEqual(h.puts().map((call) => call.url), ["/api/v1/settings/scheduling"]);
  const none = actionHarness();
  const result = await none.previewSettingsChange(formFor(), none.expected, "Nothing changed");
  assert.equal(result.error.code, "NO_CHANGES");
  assert.equal(none.puts().length, 0);
});

test("stale versions, invalid values, missing reasons and forged forms never reach a PUT", async () => {
  const stale = actionHarness();
  const result = await stale.previewSettingsChange({ ...formFor(), s1_weight: "30" }, { ...stale.expected, scheduling_version: 3 }, "Lower clustering");
  assert.equal(result.error.status, 409);
  const seatingStale = await stale.saveSettingsChange({ ...formFor(), seating_percentage: "60" }, { ...stale.expected, seating_version: 2 }, "Lower clustering", true);
  assert.equal(seatingStale.error.status, 409);
  const invalid = await stale.previewSettingsChange({ ...formFor(), max_sessions_per_exam: "20" }, stale.expected, "x");
  assert.equal(invalid.error.status, 422);
  assert.match(invalid.error.fieldErrors.max_sessions_per_exam, /1 to 10/);
  assert.match(invalid.error.fieldErrors.reason, /5–500/);
  const forged = await stale.previewSettingsChange({ minimum_break_minutes: 0 }, stale.expected, "Forged request");
  assert.equal(forged.error.code, "INVALID_INPUT");
  assert.equal(stale.puts().length, 0);
});

test("saving needs explicit confirmation, saves both changes and returns the reloaded versions", async () => {
  const h = actionHarness();
  const form = { ...formFor(), fairness_enabled: true, max_individual_increase_points: "2.5", seating_percentage: "60" };
  const unconfirmed = await h.saveSettingsChange(form, h.expected, "Enable fairness", false);
  assert.equal(unconfirmed.error.code, "CONFIRMATION_REQUIRED");
  assert.equal(h.calls.length, 0);
  const saved = await h.saveSettingsChange(form, h.expected, "Enable fairness", true);
  assert.equal(saved.ok, true);
  assert.deepEqual(saved.data.saved, ["scheduling", "seating"]);
  assert.equal(saved.data.problem, null);
  assert.ok(h.puts().every((call) => call.query.dry_run === false));
  assert.equal(saved.data.scheduling.version, 5);
  assert.equal(saved.data.scheduling.fingerprint, "e".repeat(64));
  assert.equal(saved.data.scheduling.settings.fairness.enabled, true);
  assert.equal(saved.data.scheduling.settings.fairness.max_individual_increase_points, 2.5);
  assert.equal(saved.data.seating.ruleset_version, 8);
  assert.deepEqual(saved.data.seating.default, { percentage: 60, version: 4 });
  assert.deepEqual(saved.data.seating.venue_overrides, [{ venue_id: "A304", percentage: 80 }]);
  assert.equal("history" in saved.data.scheduling || "updated_by" in saved.data.scheduling, false);
});

test("a rejected settings save stops before seating, and a failed seating save is reported as partial", async () => {
  const rejected = actionHarness({ schedulingFails: true });
  const form = { ...formFor(), s2_weight: "10", seating_percentage: "60" };
  const result = await rejected.saveSettingsChange(form, rejected.expected, "Adjust weights", true);
  assert.equal(result.error.status, 409);
  assert.deepEqual(rejected.puts().map((call) => call.url), ["/api/v1/settings/scheduling"]);
  const partial = actionHarness({ seatingFails: true });
  const saved = await partial.saveSettingsChange(form, partial.expected, "Adjust weights", true);
  assert.equal(saved.ok, true);
  assert.deepEqual(saved.data.saved, ["scheduling"]);
  assert.match(saved.data.problem, /scheduling settings were saved, but the default seating change was not \(HTTP 403/);
  const seatingOnly = actionHarness({ seatingFails: true });
  const denied = await seatingOnly.saveSettingsChange({ ...formFor(), seating_percentage: "60" }, seatingOnly.expected, "Adjust seating", true);
  assert.equal(denied.error.status, 403);
});

test("invalid saved settings are repaired only with replace_invalid, and the role guard runs before any request", async () => {
  const broken = schedulingResponse({ status: "invalid", fingerprint: null, problems: [{ rule: "SCHEDULING", message: "bad" }],
    settings: { ...defaults(), scheduling: { ...defaults().scheduling, minimum_break_minutes: 5000 } } });
  const h = actionHarness({ scheduling: broken });
  const result = await h.previewSettingsChange({ ...lib.settingsForm(lib.schedulingReview(broken), lib.seatingReview(seatingResponse())), minimum_break_minutes: "60" }, h.expected, "Repair settings");
  assert.equal(result.ok, true);
  assert.equal(result.data.replace_invalid, true);
  assert.equal(h.puts()[0].query.replace_invalid, true);
  const denied = actionHarness({ denied: true });
  await assert.rejects(denied.saveSettingsChange(formFor(), denied.expected, "Anything", true), (error) => error.destination === "/");
  assert.equal(denied.calls.length, 0);
});

// ---- Editor ----

const icons = load("src/components/icon.tsx", { "react/jsx-runtime": jsxRuntime });
const ui = load("src/components/console/ui.tsx", { "react/jsx-runtime": jsxRuntime, "next/link": { default: (props) => createElement("a", props) }, "@/components/icon": icons });
const presentation = load("src/lib/examination-presentation.ts", {});
const styles = { fieldClass: "field", primaryButton: "primary", secondaryButton: "secondary" };
const preferencesSummary = load("src/app/(examination)/exams/generate/_components/preferences-summary.tsx", {
  "react/jsx-runtime": jsxRuntime, "@/components/console/ui": ui, "@/lib/examination-settings": lib,
});

function review(scheduling = schedulingResponse(), seating = seatingResponse()) {
  return { period: { period_code: "FINAL-CYCLE", period_name: "Semester 2 Finals" }, mapping: [], modules: [], draft_count: 0, locked: false, warnings: [],
    scheduling: lib.schedulingReview(scheduling), seating: lib.seatingReview(seating) };
}

function editor(actions, props = {}) {
  const slots = [];
  let cursor = 0;
  let work = Promise.resolve();
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial;
      return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value; }];
    },
    useRef(initial) { const index = cursor++; if (!(index in slots)) slots[index] = { current: initial }; return slots[index]; },
    useTransition() { cursor++; return [false, (callback) => { work = Promise.resolve(callback()); }]; },
  };
  const { InstitutionSettingsEditor: SettingsReview } = load("src/app/(examination)/exams/settings/_components/institution-settings-editor.tsx", {
    "react/jsx-runtime": jsxRuntime, react, "@/components/console/ui": ui, "@/lib/examination-presentation": presentation,
    "@/lib/examination-settings": lib, "@/lib/examination-settings-actions": actions, "../../generate/_components/period-calendar": styles,
    "../../generate/_components/preferences-summary": preferencesSummary,
  });
  const allProps = { review: review(), ...props };
  const render = () => { cursor = 0; return SettingsReview(allProps); };
  // Expand function components (Card, Badge, HardRuleDescriptions) so their children can be found.
  const expand = (node) => !node || typeof node !== "object" ? node : Array.isArray(node) ? node.map(expand)
    : typeof node.type === "function" && node.type !== SettingsReview ? expand(node.type(node.props)) : { ...node, props: { ...node.props, children: expand(node.props?.children) } };
  const nodes = (node) => !node || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
  const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
  const all = () => nodes(expand(render()));
  const find = (predicate, label) => { const found = all().find(predicate); assert.ok(found, `Expected ${label}`); return found; };
  return {
    all, text, wait: () => work, props: allProps,
    input: (id) => find((node) => node.type === "input" && node.props.id === id, id),
    button: (label) => find((node) => node.type === "button" && text(node) === label, label),
    markup: () => renderToStaticMarkup(createElement(() => expand(render()))),
  };
}

test("the editor shows every supported section with real values, defaults and read-only mandatory rules", () => {
  const e = editor({ previewSettingsChange: () => assert.fail("no request on render"), saveSettingsChange: () => assert.fail("no request on render") });
  const html = e.markup();
  for (const text of ["Recommended Settings", "Exam Rules", "Permitted exam start times", "08:30", "13:30", "Minimum break between exams", "Maximum rooms per examination",
    "Maximum sessions per examination", "Venue and seating", "Current saved value: <strong class=\"text-ink\">50%</strong>", "A304: 80%",
    "Student Workload", "S3 · Same day or very short gap", "Default: 30.", "Fairness protection", "Optimized mode", "baseline",
    "Fairness protection is disabled", "Search time", "Advanced Settings", "Random seed", "Mandatory rules H1–H11", "Cannot be disabled",
    "11 mandatory rules confirmed", "Saved for every examination cycle", "For this generation only", "Institution-wide"]) {
    assert.ok(html.includes(text), `Missing ${text}`);
  }
  assert.match(html, /<details[^>]*><summary[^>]*>Advanced Settings/);
  // No control can switch off a hard rule or enable the unsupported automatic relaxation.
  const controls = e.all().filter((node) => node.type === "input" && node.props.type === "checkbox").map((node) => e.text(node) || node.props.id);
  assert.equal(controls.length, 2);
  assert.ok(!html.includes("automatic_relaxation"));
  assert.equal(e.input("setting-max_rooms_per_exam").props.placeholder, "No limit");
  assert.equal(e.input("setting-max_individual_increase_points").props.disabled, true);
  assert.equal(e.button("Review changes").props.disabled, true);
});

test("edits show inline errors, reset restores the saved values, and dirty state is reported", () => {
  const dirty = [];
  const e = editor({ previewSettingsChange: () => assert.fail(), saveSettingsChange: () => assert.fail() }, { onDirty: (value) => dirty.push(value) });
  e.input("setting-max_sessions_per_exam").props.onChange({ target: { value: "12" } });
  assert.match(e.markup(), /Enter a whole number from 1 to 10\./);
  assert.match(e.markup(), /Some values need correcting/);
  assert.equal(e.button("Review changes").props.disabled, true);
  e.button("Reset changes").props.onClick();
  assert.equal(e.input("setting-max_sessions_per_exam").props.value, "3");
  assert.deepEqual(dirty, [true, false]);
});

test("institution preferences start compact, preserve all values and reveal advanced validation errors", () => {
  const e = editor({ previewSettingsChange: () => assert.fail("no request on render"), saveSettingsChange: () => assert.fail("no request on render") });
  const disclosure = (title) => e.all().find((node) => node.type === "details" && e.text(node.props.children?.[0]).startsWith(title));
  for (const title of ["Exam Rules", "Student Workload", "Fairness", "Advanced Settings"]) assert.equal(disclosure(title).props.open, false, title);
  const base = lib.settingsForm(e.props.review.scheduling, e.props.review.seating);
  for (const name of Object.keys(lib.NUMBER_FIELDS)) {
    assert.equal(e.input(`setting-${name}`).props.value, base[name], name);
    assert.equal(e.all().filter((node) => node.type === "input" && node.props.id === `setting-${name}`).length, 1, name);
  }
  assert.match(e.markup(), /Recommended Settings/);
  e.input("setting-random_seed").props.onChange({ target: { value: "-1" } });
  assert.equal(disclosure("Advanced Settings").props.open, true);
  assert.equal(disclosure("Search and calculation details").props.open, true);
  assert.equal(e.button("Review changes").props.disabled, true);
  e.button("Reset changes").props.onClick();
  assert.equal(e.input("setting-random_seed").props.value, base.random_seed);
});

test("review, confirm and save call the actions in order and hand the reloaded settings to the wizard", async () => {
  const calls = [];
  const saved = [];
  const next = review(schedulingResponse({ version: 5, fingerprint: "e".repeat(64), settings: { ...defaults(), scheduling: { ...defaults().scheduling, minimum_break_minutes: 90 } } }));
  const e = editor({
    previewSettingsChange: async (...args) => { calls.push(["preview", ...args]); return { ok: true, data: { scheduling_fields: ["scheduling.minimum_break_minutes"], seating: null, drafts: 2, approved: 1, replace_invalid: false } }; },
    saveSettingsChange: async (...args) => { calls.push(["save", ...args]); return { ok: true, data: { saved: ["scheduling"], problem: null, scheduling: next.scheduling, seating: next.seating } }; },
  }, { onSaved: (...args) => saved.push(args), onDirty: () => {} });
  e.input("setting-minimum_break_minutes").props.onChange({ target: { value: "90" } });
  assert.match(e.markup(), /Unsaved changes: Minimum break between exams \(minutes\)/);
  e.button("Review changes").props.onClick();
  await e.wait();
  assert.equal(calls.length, 0, "a reason is required before the dry run");
  assert.match(e.markup(), /Enter a reason of 5–500 characters/);
  e.input("settings-reason").props.onChange({ target: { value: "Faculty board decision" } });
  e.button("Review changes").props.onClick();
  await e.wait();
  assert.deepEqual(calls[0].slice(2), [{ scheduling_version: 4, seating_version: 3 }, "Faculty board decision"]);
  const html = e.markup();
  assert.match(html, /this affects every examination cycle/);
  assert.match(html, /2 draft and 1 approved timetable\(s\) exist across all cycles/);
  assert.match(html, /Legacy timetables that depend on active settings may become stale/);
  assert.equal(e.button("Save changes").props.disabled, true);
  e.all().find((node) => node.type === "label" && e.text(node).includes("apply to all examination cycles"))
    .props.children.find((child) => child?.type === "input").props.onChange({ target: { checked: true } });
  e.button("Save changes").props.onClick();
  await e.wait();
  assert.equal(calls[1][0], "save");
  assert.equal(calls[1][4], true);
  assert.equal(calls[1][1].minimum_break_minutes, "90");
  assert.equal(saved.length, 1);
  assert.equal(saved[0][0].fingerprint, "e".repeat(64));
  assert.match(e.markup(), /The next generation uses settings version 5/);
});

test("an edit after the dry run discards it, so a stale preview can never be saved", async () => {
  const calls = [];
  const e = editor({
    previewSettingsChange: async () => { calls.push("preview"); return { ok: true, data: { scheduling_fields: ["optimization.s1_weight"], seating: null, drafts: 0, approved: 0, replace_invalid: false } }; },
    saveSettingsChange: async () => { calls.push("save"); },
  });
  e.input("setting-s1_weight").props.onChange({ target: { value: "40" } });
  e.input("settings-reason").props.onChange({ target: { value: "Prefer spacing" } });
  e.button("Review changes").props.onClick();
  await e.wait();
  assert.ok(e.button("Save changes"));
  e.input("setting-s1_weight").props.onChange({ target: { value: "41" } });
  assert.ok(e.button("Review changes"));
  assert.equal(e.all().some((node) => node.type === "button" && e.text(node) === "Save changes"), false);
  assert.deepEqual(calls, ["preview"]);
});
