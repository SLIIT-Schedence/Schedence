import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";

// Focused UI, action and transport checks. No service mutation, database or browser session is used.
function load(file, mocks = {}) {
  const code = ts.transpileModule(readFileSync(path.join(import.meta.dirname, "..", file), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const mod = { exports: {} };
  new Function("require", "module", "exports", code)((name) => {
    assert.ok(Object.hasOwn(mocks, name), `Unexpected dependency ${name}`);
    return mocks[name];
  }, mod, mod.exports);
  return mod.exports;
}
const contracts = load("src/lib/form-template-contracts.ts");
const sendingContracts = load("src/lib/form-sending-contracts.ts");
const institution = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const id = "33333333-3333-4333-8333-333333333333";
const ok = (data) => ({ ok: true, data });
const failure = (status) => ({ ok: false, error: { status, code: status === 409 ? "REVISION_CONFLICT" : "VALIDATION_ERROR", message: "Backend validation" } });
const question = (patch = {}) => ({ id: "custom.notes", kind: "CUSTOM", label: "Notes", help_text: "", field_key: null,
  field_type: "text", required: false, choices: [], minimum: null, maximum: null, matrix: null, module_reference: null, ...patch });
const locked = () => question({ id: "system.teaching_availability", kind: "LOCKED", label: "Teaching availability",
  field_key: "teaching_availability", field_type: "availability_matrix", required: true,
  matrix: { days: ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
    day_parts: ["MORNING", "AFTERNOON"], values: ["Preferred", "Available", "Not Preferred", "Not Available"] } });
const template = () => ({ id, institution_id: institution, schema_version: 1, revision: 4, status: "DRAFT",
  preset_id: contracts.LECTURER_PREFERENCES, title: "My form", description: "A saved draft", questions: [locked(), question()],
  created_by: id, updated_by: id, created_at: "2026-10-21T00:00:00Z", updated_at: "2026-10-21T00:00:00Z" });

test("locked questions retain their schema through wording edits and reorder", () => {
  const original = [locked(), question()];
  const rows = original.map(contracts.questionDraft);
  rows[0].question = { ...rows[0].question, label: "When can you teach?", help_text: "Choose each day part." };
  const moved = contracts.moveQuestion(rows, 0, 1);
  const result = contracts.validateDraft("Title", "", moved, original);
  assert.equal(result.valid, true);
  assert.deepEqual(result.questions[1], { ...original[0], label: "When can you teach?", help_text: "Choose each day part." });
  assert.equal(original[0].label, "Teaching availability");
  assert.equal(contracts.changeFieldType(rows[0], "number"), rows[0]);
});

for (const [name, change] of [
  ["remove", (rows) => rows.slice(1)],
  ["field key", (rows) => { rows[0].question.field_key = "other"; return rows; }],
  ["field type", (rows) => { rows[0].question.field_type = "text"; return rows; }],
  ["matrix", (rows) => { rows[0].question.matrix.day_parts = ["09:00"]; return rows; }],
  ["required", (rows) => { rows[0].question.required = false; return rows; }],
]) test(`client validation detects locked ${name} changes before save`, () => {
  const original = [locked(), question()];
  const rows = structuredClone(original).map(contracts.questionDraft);
  assert.equal(contracts.validateDraft("Title", "", change(rows), original).valid, false);
});

test("custom questions add and reorder without mutating existing questions", () => {
  const a = contracts.customQuestion(), b = contracts.customQuestion();
  assert.notEqual(a.question.id, b.question.id);
  assert.equal(a.question.kind, "CUSTOM");
  assert.equal(a.question.field_key, null);
  const rows = [a, b];
  assert.equal(contracts.moveQuestion(rows, 0, -1), rows);
  assert.deepEqual(contracts.moveQuestion(rows, 0, 1), [b, a]);
  assert.deepEqual(rows, [a, b]);
});

test("choices and numeric fields validate without losing partially edited text", () => {
  let row = contracts.changeFieldType(contracts.questionDraft(question()), "multi_choice");
  row.choicesText = "First choice\nSecond choice\n";
  let result = contracts.validateDraft("Title", "", [row], []);
  assert.equal(result.valid, true);
  assert.deepEqual(result.questions[0].choices, ["First choice", "Second choice"]);
  assert.ok(row.choicesText.endsWith("\n"));
  row.choicesText = "Same\nSame";
  assert.equal(contracts.validateDraft("Title", "", [row], []).valid, false);
  row.choicesText = "";
  assert.equal(contracts.validateDraft("Title", "", [row], []).valid, false);
  row = contracts.changeFieldType(row, "number");
  row.minimumText = "3.5"; row.maximumText = "8";
  result = contracts.validateDraft("Title", "", [row], []);
  assert.equal(result.questions[0].minimum, 3.5);
  assert.equal(result.questions[0].maximum, 8);
  row.maximumText = "2";
  assert.equal(contracts.validateDraft("Title", "", [row], []).valid, false);
  row.maximumText = "Infinity";
  assert.equal(contracts.validateDraft("Title", "", [row], []).valid, false);
  row = contracts.changeFieldType(row, "text");
  assert.equal(contracts.validateDraft("Title", "", [row], []).valid, true);
});

test("blank labels, duplicate IDs, excessive questions and oversized content are rejected", () => {
  const rows = [contracts.questionDraft(question())];
  for (const [title, description, questions] of [["", "", rows], ["x".repeat(201), "", rows],
    ["Title", "x".repeat(4001), rows], ["Title", "", [...rows, ...rows]], ["Title", "", Array(101).fill(rows[0])]]) {
    assert.equal(contracts.validateDraft(title, description, questions, []).valid, false);
  }
  rows[0].question.label = " ";
  assert.equal(contracts.validateDraft("Title", "", rows, []).valid, false);
});

test("real API adapter sends institution-scoped requests and revision without alternate storage", async () => {
  const calls = [];
  const adapter = load("src/lib/form-templates.ts", { "server-only": {}, "@/lib/service-api": {
    serviceUrl: (name, fallback) => { assert.equal(name, "TEACHING_SERVICE_URL"); return fallback; },
    serviceRequest: async (service, path, options) => { calls.push({ path, options }); assert.equal(service.baseUrl(), "http://localhost:8004"); return ok(template()); },
  } });
  await adapter.getFormPresets(institution);
  await adapter.listFormTemplates(institution, 2);
  await adapter.getFormTemplate(institution, id);
  await adapter.createFormTemplate({ institution_id: institution, title: "Blank", description: "" });
  const body = { institution_id: institution, expected_revision: 4, title: "Updated", description: "", questions: [locked()] };
  await adapter.saveFormTemplate(id, body);
  await adapter.duplicateFormTemplate(institution, id);
  assert.equal(calls[0].path, "/api/v1/teaching/form-templates/presets");
  assert.deepEqual(calls[1].options.query, { institution_id: institution, page: 2, page_size: 20 });
  assert.deepEqual(calls[2].options.query, { institution_id: institution });
  assert.equal(calls[3].options.method, "POST");
  assert.equal(calls[4].options.method, "PUT");
  assert.deepEqual(calls[4].options.body, body);
  assert.equal(calls[5].path, `/api/v1/teaching/form-templates/${id}/duplicate`);
  assert.deepEqual(calls[5].options.body, { institution_id: institution });
});

test("server actions enforce active institution access and keep backend errors", async () => {
  const calls = [], invalidated = [];
  let context = { institutions: [{ id: institution, status: "ACTIVE" }, { id: other, status: "DISABLED" }] };
  let result = ok(template());
  const action = load("src/lib/form-template-actions.ts", {
    "next/cache": { revalidatePath: (...args) => invalidated.push(args) },
    "@/lib/academic": { getAcademicContext: async () => context },
    "@/lib/identity-records": { isUuid: (value) => /^[0-9a-f-]{36}$/.test(value) },
    "@/lib/form-templates": Object.fromEntries(["createFormTemplate", "saveFormTemplate", "duplicateFormTemplate", "getFormTemplate"].map((name) =>
      [name, async (...args) => { calls.push({ name, args }); return result; }])),
  });
  const body = { institution_id: institution, title: "Title", description: "", expected_revision: 4, questions: [] };
  assert.equal((await action.createDraftForm({ ...body, institution_id: other })).ok, false);
  assert.equal((await action.createDraftForm(null)).ok, false);
  assert.equal((await action.saveDraftForm("invalid", body)).ok, false);
  assert.equal(calls.length, 0);
  result = failure(409);
  assert.deepEqual(await action.saveDraftForm(id, body), result);
  assert.equal(calls[0].args[1].expected_revision, 4);
  assert.equal(invalidated.length, 0);
  result = ok(template());
  await action.createDraftForm(body); await action.duplicateDraftForm(institution, id); await action.reloadDraftForm(institution, id);
  assert.equal(invalidated.length, 2);
  context = null;
  const before = calls.length;
  assert.equal((await action.reloadDraftForm(institution, id)).ok, false);
  assert.equal(calls.length, before);
});

const primitives = {
  Card: ({ children }) => React.createElement("section", {}, children),
  EmptyState: ({ title, children }) => React.createElement("div", {}, title, children),
  Badge: ({ children }) => React.createElement("span", {}, children),
  PageHeader: ({ title, actions }) => React.createElement("header", {}, title, actions),
};
const styles = load("src/app/(academic)/academic/forms/_components/styles.ts");
const preview = load("src/app/(academic)/academic/forms/_components/form-preview.tsx", {
  "react/jsx-runtime": jsxRuntime, "@/components/console/ui": primitives,
  "@/lib/form-template-contracts": contracts, "./styles": styles,
});

test("preview renders fourteen day-part selectors with backend values and no response action", () => {
  const markup = renderToStaticMarkup(React.createElement(preview.FormPreview, { title: "Preview title", description: "Description", questions: [locked()] }));
  assert.equal((markup.match(/<select/g) ?? []).length, 14);
  for (const value of ["Monday", "Sunday", "Morning", "Afternoon", "Preferred", "Available", "Not Preferred", "Not Available"]) assert.ok(markup.includes(value), value);
  assert.ok(markup.includes("<fieldset disabled="));
  assert.ok(!markup.includes("09:00"));
  assert.ok(!markup.includes("<form"));
  assert.ok(markup.includes("Responses are not collected here"));
});

function nodes(value) {
  if (Array.isArray(value)) return value.flatMap(nodes);
  if (!value || typeof value !== "object" || !value.props) return [];
  return [value, ...nodes(value.props.children), ...nodes(value.props.actions)];
}
const text = (node) => typeof node === "string" ? node : Array.isArray(node) ? node.map(text).join("") : node?.props ? text(node.props.children) : "";

test("editor keeps edits and revision after 409, retains reload controls, and requires confirmation to discard", async () => {
  const states = [], requests = [], work = [];
  let index = 0, confirm = false;
  const previousWindow = globalThis.window;
  globalThis.window = { confirm: () => confirm };
  const fakeReact = {
    useState: (initial) => { const i = index++; if (!(i in states)) states[i] = typeof initial === "function" ? initial() : initial; return [states[i], (next) => { states[i] = typeof next === "function" ? next(states[i]) : next; }]; },
    useEffect: () => {}, useTransition: () => [false, (fn) => work.push(fn())],
  };
  const editor = load("src/app/(academic)/academic/forms/_components/form-editor.tsx", {
    "react/jsx-runtime": jsxRuntime, react: fakeReact, "next/link": { default: "a" },
    "next/navigation": { useRouter: () => ({ push: () => assert.fail("Unexpected navigation") }) },
    "@/components/console/ui": primitives, "@/components/icon": { Icon: () => null },
    "@/lib/form-template-contracts": contracts, "./styles": styles, "./form-preview": preview,
    "@/lib/form-sending-contracts": sendingContracts,
    "@/lib/form-template-actions": {
      saveDraftForm: async (...args) => { requests.push(args); return failure(409); },
      duplicateDraftForm: async () => assert.fail("Unsaved draft must not duplicate"),
      reloadDraftForm: async () => ok({ ...template(), revision: 5, title: "Newer saved title" }),
    },
  });
  const render = () => { index = 0; return editor.FormEditor({ initial: template(), institutionName: "Institution" }); };
  const button = (tree, label) => nodes(tree).find((n) => n.type === "button" && text(n).includes(label));
  const titleInput = (tree) => nodes(tree).find((n) => n.type === "input" && typeof n.props.value === "string");
  try {
    let tree = render();
    assert.ok(nodes(tree).some((n) => n.type === "a" && text(n) === "Send form"));
    titleInput(tree).props.onChange({ target: { value: "My unsaved title" } });
    tree = render();
    assert.equal(button(tree, "Send form").props.disabled, true);
    assert.equal(button(tree, "Duplicate").props.disabled, true);
    button(tree, "Save draft").props.onClick(); await work.pop();
    assert.equal(requests[0][1].expected_revision, 4);
    tree = render();
    assert.equal(titleInput(tree).props.value, "My unsaved title");
    assert.equal(button(tree, "Save draft").props.disabled, true);
    assert.ok(button(tree, "Reload latest draft"));
    titleInput(tree).props.onChange({ target: { value: "Still editing" } });
    tree = render();
    assert.ok(button(tree, "Reload latest draft"), "Conflict action remains after further edits");
    button(tree, "Reload latest draft").props.onClick();
    assert.equal(work.length, 0);
    confirm = true;
    button(tree, "Reload latest draft").props.onClick(); await work.pop();
    tree = render();
    assert.equal(titleInput(tree).props.value, "Newer saved title");
    assert.equal(button(tree, "Save draft").props.disabled, false);
    assert.equal(button(tree, "Duplicate").props.disabled, false);
  } finally { globalThis.window = previousWindow; }
});
