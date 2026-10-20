import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import * as jsxRuntime from "react/jsx-runtime";

// Single / Both Semesters switching is local state: no navigation, no refetch, both forms stay mounted.
function load(source, mocks) {
  const code = ts.transpileModule(readFileSync(path.join(import.meta.dirname, "..", source), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const result = { exports: {} };
  new Function("require", "module", "exports", code)((name) => {
    assert.ok(Object.hasOwn(mocks, name), `Unexpected dependency ${name}`); return mocks[name];
  }, result, result.exports);
  return result.exports;
}

function harness(initialBoth, search = "") {
  let cursor = 0, dirty = false;
  const states = [], effects = [], replaced = [], listeners = {};
  const Single = () => null, Both = () => null, Activity = () => null;
  globalThis.window = {
    location: { href: `https://app.test/exams/generate${search}`, get search() { return search; } },
    history: { state: { marker: 1 }, replaceState: (...args) => { replaced.push(args); search = new URL(args[2], "https://app.test").search; } },
    addEventListener: (type, fn) => { listeners[type] = fn; }, removeEventListener: (type) => { delete listeners[type]; },
  };
  const lib = load("src/app/(examination)/exams/generate/_components/generation-modes.tsx", {
    "react/jsx-runtime": jsxRuntime,
    react: { Activity, useEffect: (fn) => { effects.push(fn); },
      useState: (value) => { const i = cursor++; if (!(i in states)) states[i] = value;
        return [states[i], (next) => { if (states[i] !== next) dirty = true; states[i] = next; }]; } },
    "./both-semesters-form": { BothSemestersForm: Both }, "./generation-form": { GenerationForm: Single },
  });
  const cycles = [{ period_code: "A" }];
  // React re-renders at once when state is set during render; repeat until stable.
  const render = () => { let out; for (let n = 0; n < 5; n++) { cursor = 0; effects.length = 0; dirty = false; out = lib.GenerationModes({ cycles, initialBoth }); if (!dirty) break; } return out; };
  const setInitial = (value) => { initialBoth = value; };
  const nodes = (n) => !n || typeof n !== "object" ? [] : Array.isArray(n) ? n.flatMap(nodes) : [n, ...nodes(n.props?.children)];
  const find = (type) => nodes(render()).find((n) => n.type === type);
  const button = (label) => nodes(render()).find((n) => n.type === "button" && n.props.children === label);
  return { render, find, button, setInitial, Single, Both, Activity, replaced, listeners, effects,
    mode: (type) => nodes(render()).find((n) => n.type === Activity && n.props.children.type === type).props.mode };
}

test("switching modes only flips local state and keeps both forms mounted", () => {
  const h = harness(false);
  assert.deepEqual([h.mode(h.Single), h.mode(h.Both)], ["visible", "hidden"]);
  assert.equal(h.button("Single Semester").props["aria-pressed"], true);
  h.button("Both Semesters").props.onClick();
  assert.deepEqual([h.mode(h.Single), h.mode(h.Both)], ["hidden", "visible"]);
  assert.equal(h.button("Both Semesters").props["aria-pressed"], true);
  assert.ok(h.find(h.Single) && h.find(h.Both), "neither form is unmounted");
  h.button("Single Semester").props.onClick();
  assert.deepEqual([h.mode(h.Single), h.mode(h.Both)], ["visible", "hidden"]);
});

test("the URL is mirrored with replaceState (no navigation, no history entry) and clicking the active mode does nothing", () => {
  const h = harness(false);
  h.button("Single Semester").props.onClick();
  assert.equal(h.replaced.length, 0);
  h.button("Both Semesters").props.onClick();
  assert.equal(h.replaced.length, 1);
  assert.equal(h.replaced[0][2], "/exams/generate?semesters=both");
  assert.deepEqual(h.replaced[0][0], { marker: 1 });
  h.button("Single Semester").props.onClick();
  assert.equal(h.replaced[1][2], "/exams/generate");
});

test("buttons are plain buttons, not links, and browser back/forward and server navigation resync the mode", () => {
  const h = harness(false);
  assert.equal(h.render().props.children[0].props.children.every((b) => b.type === "button" && b.props.type === "button"), true);
  h.render(); h.effects[0]();
  assert.equal(typeof h.listeners.popstate, "function");
  globalThis.window.location = { get search() { return "?semesters=both"; } };
  h.listeners.popstate();
  assert.equal(h.mode(h.Both), "visible");
  globalThis.window.location = { get search() { return ""; } };
  h.listeners.popstate();
  assert.equal(h.mode(h.Both), "hidden");
  const fromServer = harness(true);
  assert.equal(fromServer.mode(fromServer.Both), "visible");
  h.setInitial(true);
  assert.equal(h.mode(h.Both), "visible");
});
