import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as jsxRuntime from "react/jsx-runtime";

// Isolated fixtures and mocked existing API reads. No servers, credentials, database writes or requests.
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

const helpers = load("src/lib/examination-venues.ts");
const BASE = { venue_id: "F1301+F1302", venue_name: "Combined hall", building: "F", floor: 0, venue_type: "lecture_hall",
  normal_capacity: 200, exam_capacity: 100, exam_seating_percentage: 50, exam_seating_rule: "default", available_for_exams: true };
const VENUES = [BASE,
  { ...BASE, venue_id: "CyberSecLab", venue_name: "Security Lab", building: "C", venue_type: "lab", exam_capacity: 0, available_for_exams: false },
  { venue_id: "R1", venue_name: null, building: null, venue_type: null, normal_capacity: null, exam_capacity: null, available_for_exams: null },
];
const session = (session_id, rooms) => ({ session_id, module_code: "IT1010", module_name: "Programming", date: "2026-11-16", start_time: "08:30:00", end_time: "10:30:00", rooms });
const TIMETABLE = { timetable_id: 7, period_name: "Semester 1", exam_kind: "MID", draft_number: 2, status: "DRAFT",
  sessions: [session("S1", [{ venue_id: BASE.venue_id, seats: 20, exam_capacity: 100 }, { venue_id: "CyberSecLab", seats: 30, exam_capacity: 30 }]),
    session("S2", [{ venue_id: BASE.venue_id, seats: 80, exam_capacity: 100 }])] };
const filters = (params = {}) => helpers.readVenueFilters(params);
const codes = (venues) => venues.map((venue) => venue.venue_id);
const ok = (data) => ({ ok: true, data });
const fail = (status) => ({ ok: false, error: { status, message: "Service unavailable" } });

test("search and filters use recorded venue data, preserve zero capacity and do not mutate or duplicate rooms", () => {
  const before = structuredClone(VENUES);
  const assignments = helpers.venueAssignments(TIMETABLE);
  assert.deepEqual(codes(helpers.filterVenues(VENUES, filters({ q: "  combined " }), assignments)), [BASE.venue_id]);
  assert.deepEqual(codes(helpers.filterVenues(VENUES, filters({ building: "C", type: "lab", availability: "no" }), assignments)), ["CyberSecLab"]);
  assert.deepEqual(codes(helpers.filterVenues(VENUES, filters({ availability: "unknown" }), assignments)), ["R1"]);
  assert.deepEqual(codes(helpers.filterVenues(VENUES, filters({ review: "missing" }), assignments)), ["R1"]);
  assert.deepEqual(codes(helpers.filterVenues(VENUES, filters({ review: "underuse" }), assignments)), [BASE.venue_id]);
  assert.deepEqual(helpers.filterVenues(VENUES, filters({ review: "underuse" }), null), []);
  assert.deepEqual(helpers.venueOptions(VENUES), { buildings: ["C", "F"], types: ["lab", "lecture_hall"] });
  assert.equal(helpers.venueValue(VENUES[1], "exam_capacity"), "0");
  assert.equal(helpers.venueValue(VENUES[1], "available_for_exams"), "Unavailable");
  assert.equal(helpers.venueValue(VENUES[2], "exam_capacity"), null);
  assert.equal(helpers.missingVenueFields(VENUES[2]).length, 6);
  assert.deepEqual(VENUES, before);
});

test("usage counts room sessions once and uses saved exam capacity, with an explicit below-50% threshold", () => {
  const assignments = helpers.venueAssignments(TIMETABLE);
  assert.equal(assignments.get(BASE.venue_id).length, 2);
  assert.equal(assignments.get("CyberSecLab").length, 1);
  assert.equal(assignments.get(BASE.venue_id)[0].percentage, 20);
  assert.equal(assignments.get(BASE.venue_id)[0].underused, true);
  assert.equal(assignments.get(BASE.venue_id)[1].underused, false);
  const variants = helpers.venueAssignments({ sessions: [session("S", [
    { venue_id: "HALF", seats: 50, exam_capacity: 100 }, { venue_id: "OVER", seats: 101, exam_capacity: 100 },
    { venue_id: "UNKNOWN", seats: null, exam_capacity: 100 }, { venue_id: "ZERO", seats: 0, exam_capacity: 0 },
    { venue_id: "EMPTY", seats: 0, exam_capacity: 100 },
  ])] });
  assert.equal(variants.get("HALF")[0].underused, false);
  assert.equal(variants.get("OVER")[0].overCapacity, true);
  assert.equal(variants.get("UNKNOWN")[0].percentage, null);
  assert.equal(variants.get("UNKNOWN")[0].underused, false);
  assert.equal(variants.get("ZERO")[0].percentage, null);
  assert.equal(variants.get("EMPTY")[0].percentage, 0);
  assert.equal(variants.get("EMPTY")[0].underused, true);
  assert.equal(helpers.venueAssignments(null), null);
  assert.equal(helpers.venueAssignments({ sessions: [{}] }), null);
  assert.equal(helpers.venueAssignments({ sessions: [session("S", [{ seats: 2 }])] }), null);
  assert.equal(helpers.venueAssignments({ sessions: [] }).size, 0);
});

test("room identifiers preserve spaces and plus signs and invalid timetable identifiers stay explicit", () => {
  for (const code of ["F1301+F1302", "CyberSecLab", "Robotics Lab"]) assert.equal(helpers.validVenueId(code), true);
  for (const code of ["../settings", "", "X".repeat(17)]) assert.equal(helpers.validVenueId(code), false);
  assert.equal(helpers.validTimetableId("7"), true);
  for (const code of ["0", "-1", "7abc", "2147483648", "1/2"]) assert.equal(helpers.validTimetableId(code), false);
  assert.deepEqual(filters({ q: [" Hall ", "Other"], timetable: "oops", availability: "maybe", review: "anything" }),
    { q: "Hall", building: "", type: "", availability: "", review: "", timetable: "oops" });
});

const link = ({ children, ...props }) => {
  delete props.prefetch;
  return React.createElement("a", props, children);
};
const icons = load("src/components/icon.tsx", { "react/jsx-runtime": jsxRuntime });
const ui = load("src/components/console/ui.tsx", { "react/jsx-runtime": jsxRuntime, "next/link": { default: link }, "@/components/icon": icons });
const venueUi = load("src/app/(examination)/exams/venues/_components/venue-ui.tsx", { "react/jsx-runtime": jsxRuntime,
  "next/link": { default: link }, "@/components/console/ui": ui });
const refresh = { RefreshButton: ({ label }) => React.createElement("button", { type: "button" }, label) };
const errors = load("src/app/(examination)/exams/_components/read-error.tsx", {
  "react/jsx-runtime": jsxRuntime, "@/components/console/ui": ui, "./refresh-button": refresh,
});

async function resolve(element) {
  if (Array.isArray(element)) return React.Children.toArray(await Promise.all(element.map(resolve)));
  if (!React.isValidElement(element)) return element;
  if (typeof element.type === "function") return resolve(await element.type(element.props));
  if (element.type === React.Suspense) return resolve(element.props.children);
  const children = await resolve(element.props.children);
  return React.createElement(element.type, { ...element.props, key: element.key }, ...(Array.isArray(children) ? children : [children]));
}

function pages({ venues = ok(VENUES), timetables = ok({ items: [TIMETABLE], total: 1 }), selected = ok(TIMETABLE),
  blocks = ok([{ venue_id: BASE.venue_id, block_date: "2026-11-17", start_time: "09:00:00", end_time: "12:00:00", source: "maintenance", reason: "Room maintenance" }]), authorized = true } = {}) {
  const calls = [];
  const api = {
    listExamVenues: async () => { calls.push("venues"); return venues; },
    listVenueTimetables: async () => { calls.push("timetables"); return timetables; },
    getSavedTimetable: async (id) => { calls.push(["timetable", id]); return selected; },
    listExamVenueBlocks: async (id) => { calls.push(["blocks", id]); return blocks; },
  };
  const common = { "react/jsx-runtime": jsxRuntime, react: React, "next/link": { default: link },
    "next/form": { default: (props) => React.createElement("form", props) },
    "@/components/console/ui": ui, "@/lib/examination": api, "@/lib/examination-venues": helpers,
    "@/lib/examination-access": { requireExaminationAdmin: async () => {
      calls.push("authorize"); if (!authorized) throw new Error("unauthorized");
    } },
  };
  const catalog = load("src/app/(examination)/exams/venues/page.tsx", { ...common,
    "../_components/read-error": errors, "../_components/refresh-button": refresh, "./_components/venue-ui": venueUi,
  });
  const detail = load("src/app/(examination)/exams/venues/[id]/page.tsx", { ...common,
    "next/navigation": { notFound: () => { throw new Error("not-found"); } },
    "../../_components/read-error": errors, "../../_components/refresh-button": refresh, "../_components/venue-ui": venueUi,
  });
  return { calls,
    catalog: async (params = {}) => renderToStaticMarkup(await resolve(React.createElement(catalog.default, { searchParams: Promise.resolve(params) }))),
    detail: async (id = BASE.venue_id, params = {}) => renderToStaticMarkup(await resolve(React.createElement(detail.default, { params: Promise.resolve({ id }), searchParams: Promise.resolve(params) }))),
  };
}

test("venue table shows source capacities and missing data; assignments stay unknown until selected", async () => {
  const h = pages();
  const html = await h.catalog();
  for (const text of ["Venues", "Read only", "Search rooms", "Room type", "Building", "Capacity", "Effective exam capacity", "Exam availability",
    "Assigned exams", "F1301+F1302", "Combined hall", "200", "100", "Unavailable", "Not available", "1 with missing data", "Select a saved timetable",
    'href="/exams/venues/F1301%2BF1302"', 'scope="col"', 'scope="row"']) assert.ok(html.includes(text), text);
  assert.ok(!html.includes("Coming Soon"));
  assert.ok(!html.includes("0 assigned exam sessions"));
  assert.ok(!html.includes("Possible underuse:"));
  assert.deepEqual(h.calls, ["authorize", "venues", "timetables"]);
  const selected = await pages().catalog({ timetable: "7", review: "underuse" });
  assert.match(selected, /2 assigned exam sessions/);
  assert.match(selected, /Possible underuse: 1/);
  assert.match(selected, /fewer than 50%/);
  assert.match(selected, /saved with that timetable/);
  assert.match(selected, /MID · Draft #2 · DRAFT · ID 7/);
  assert.match(selected, /F1301%2BF1302\?timetable=7/);
  assert.ok(!selected.includes('aria-label="View details for room CyberSecLab"'));
});

test("table distinguishes empty results, unavailable assignments and incomplete timetable reads", async () => {
  assert.match(await pages({ venues: ok([]) }).catalog(), /No examination venues available/);
  assert.match(await pages().catalog({ q: "no-such-room" }), /No rooms match these filters/);
  assert.match(await pages().catalog({ page: "99" }), /This page is empty/);
  assert.match(await pages().catalog({ review: "underuse" }), /Room usage cannot be assessed/);
  for (const status of [403, 503]) {
    const html = await pages({ venues: fail(status) }).catalog();
    assert.match(html, /Couldn&#x27;t load venues/);
    assert.ok(!html.includes("No examination venues available"));
    const partial = await pages({ selected: fail(status) }).catalog({ timetable: "7" });
    assert.match(partial, /Combined hall/);
    assert.match(partial, /Couldn&#x27;t load assigned exams/);
    assert.ok(!partial.includes("0 assigned exam sessions"));
    assert.ok(!partial.includes("Possible underuse:"));
  }
  const choices = await pages({ timetables: fail(503) }).catalog();
  assert.match(choices, /Combined hall/);
  assert.match(choices, /Timetable choices could not be loaded/);
  const incomplete = await pages({ selected: ok({ ...TIMETABLE, sessions: [{}] }) }).catalog({ timetable: "7" });
  assert.match(incomplete, /Assignment data is incomplete/);
  assert.ok(!incomplete.includes("0 assigned exam sessions"));
  const invalid = pages();
  assert.match(await invalid.catalog({ timetable: "7oops" }), /Select a valid saved timetable/);
  assert.ok(!invalid.calls.some((call) => Array.isArray(call) && call[0] === "timetable"));
});

test("pagination and room links preserve active filters and selected timetable", async () => {
  const many = Array.from({ length: 30 }, (_, i) => ({ ...BASE, venue_id: `R${100 + i}`, venue_name: null }));
  const h = pages({ venues: ok(many) });
  const first = await h.catalog({ building: "F", type: "lecture_hall", availability: "yes", review: "missing", timetable: "7" });
  assert.match(first, /building=F&amp;type=lecture_hall&amp;availability=yes&amp;review=missing&amp;timetable=7&amp;page=2/);
  const second = await h.catalog({ building: "F", page: "2", timetable: "7" });
  assert.equal((second.match(/aria-label="View details for room/g) ?? []).length, 5);
  assert.ok(second.includes("R125"));
  assert.ok(!second.includes("R100"));
});

test("details use the existing venue list, show saved usage and blocks, and preserve ground floor zero", async () => {
  const h = pages();
  const html = await h.detail(BASE.venue_id, { timetable: "7" });
  for (const text of ["F1301+F1302", "Read only", "Floor", "50%", "default", "200", "100", "2 assigned exam sessions", "Programming",
    "20%", "Possible underuse", "2026-11-16", "08:30:00", "Recorded availability blocks", "maintenance", "Room maintenance", "2026-11-17"]) assert.ok(html.includes(text), text);
  assert.match(html, /Floor<\/dt><dd[^>]*>0<\/dd>/);
  assert.ok(!html.includes("Save changes"));
  assert.deepEqual(h.calls, ["authorize", "venues", "timetables", ["timetable", 7], ["blocks", BASE.venue_id]]);
  const unused = await pages().detail("R1", { timetable: "7" });
  assert.match(unused, /Missing data:/);
  assert.match(unused, /No assigned exam sessions in this timetable/);
  const unknown = await pages().detail();
  assert.match(unknown, /Select a saved timetable/);
  assert.ok(!unknown.includes("No assigned exam sessions in this timetable"));
});

test("authorization runs before reads; invalid and absent venue IDs do not trigger assignment or block reads", async () => {
  for (const route of ["catalog", "detail"]) {
    const denied = pages({ authorized: false });
    await assert.rejects(denied[route](), /unauthorized/);
    assert.deepEqual(denied.calls, ["authorize"]);
  }
  const invalid = pages();
  await assert.rejects(invalid.detail("../settings"), /not-found/);
  assert.deepEqual(invalid.calls, ["authorize"]);
  const missing = pages();
  await assert.rejects(missing.detail("NOTFOUND"), /not-found/);
  assert.deepEqual(missing.calls, ["authorize", "venues"]);
  const unavailable = await pages({ venues: fail(403) }).detail();
  assert.match(unavailable, /does not have access/);
  const partial = await pages({ blocks: fail(503) }).detail(BASE.venue_id, { timetable: "7" });
  assert.match(partial, /Room details/);
  assert.match(partial, /Couldn&#x27;t load availability blocks/);
  assert.ok(!partial.includes("No recorded availability blocks"));
});

test("venue API wrappers use only existing GET routes, keep codes intact, and include MID timetables", async () => {
  const calls = [];
  const api = load("src/lib/examination.ts", { "server-only": {}, react: { cache: (fn) => fn },
    "@/lib/examination-api": { examinationRequest: async (...args) => { calls.push(args); return ok([]); } },
  });
  await api.listExamVenues();
  await api.listVenueTimetables();
  await api.listExamVenueBlocks("F1301+F1302");
  await api.getSavedTimetable(7);
  assert.deepEqual(calls.map(([url]) => url), ["/api/v1/venues", "/api/v1/timetables", "/api/v1/venue-blocks", "/api/v1/timetables/7"]);
  assert.deepEqual(calls[1][1].query, { limit: 100, offset: 0 });
  assert.equal(calls[2][1].query.venue_id, "F1301+F1302");
  for (const [, options] of calls) {
    assert.equal(options?.method ?? "GET", "GET");
    assert.equal(options?.body, undefined);
  }
  const choices = renderToStaticMarkup(React.createElement(venueUi.TimetableSelect, { result: ok({ items: [TIMETABLE], total: 101 }), selected: "120" }));
  assert.match(choices, /Showing the 1 most recent saved timetables/);
  assert.match(choices, /Timetable #120/);
});
