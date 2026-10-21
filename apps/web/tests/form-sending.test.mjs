import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";

// All transport is mocked: these tests never send a real form or write to any database.
function load(file, mocks = {}) {
  const code = ts.transpileModule(readFileSync(path.join(import.meta.dirname, "..", file), "utf8"), {
    fileName: file,
    compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const mod = { exports: {} };
  new Function("require", "module", "exports", code)((name) => {
    assert.ok(Object.hasOwn(mocks, name), `Unexpected dependency ${name}`); return mocks[name];
  }, mod, mod.exports);
  return mod.exports;
}
const contracts = load("src/lib/form-sending-contracts.ts");
const draftContracts = load("src/lib/form-template-contracts.ts");
const institution = "11111111-1111-4111-8111-111111111111", other = "22222222-2222-4222-8222-222222222222", id = "33333333-3333-4333-8333-333333333333";
const staff = "44444444-4444-4444-8444-444444444444", dept = "55555555-5555-4555-8555-555555555555", second = "66666666-6666-4666-8666-666666666666";
const lecturers = [{ staff_id: staff, staff_number: "L001", full_name: "Lecturer One", department_ids: [dept], departments: [{ id: dept, name: "Computing" }], identity_user_id: id, link_status: "READY" },
  { staff_id: second, staff_number: "L002", full_name: "Lecturer Two", department_ids: [dept], departments: [{ id: dept, name: "Computing" }], identity_user_id: null, link_status: "NO_LINK" }];
const template = { id, institution_id: institution, revision: 4, title: "Saved form", description: "", questions: [{ id: "custom.notes", label: "Notes", field_type: "text" }] };
const body = { institution_id: institution, expected_revision: 4, channel: "SHARE_LINK", audience: { kind: "SELECTED", staff_ids: [staff] }, deadline: "2030-01-01T12:00:00Z" };
const ok = (data) => ({ ok: true, data });
const failure = (status, code = "VALIDATION_ERROR") => ({ ok: false, error: { status, code, message: "Backend error" } });

test("selected, department and all audiences use authoritative staff/department IDs and include account warnings", () => {
  assert.deepEqual(contracts.departmentOptions(lecturers), [{ id: dept, name: "Computing" }]);
  for (const [kind,selected,count] of [["SELECTED",[staff],1],["DEPARTMENT",[],2],["ALL_ELIGIBLE",[],2]]) {
    const result = contracts.prepareSend(template,lecturers,kind,selected,dept,"DIRECT","2030-01-01T17:30",Date.parse("2026-01-01"));
    assert.equal(result.valid,true); assert.equal(result.recipients.length,count);
    assert.equal(result.body.deadline,"2030-01-01T12:00:00.000Z");
    assert.equal(result.body.expected_revision,4); assert.equal(result.body.audience.kind,kind);
    assert.equal(contracts.validSendBody(result.body),true);
  }
  assert.equal(contracts.recipientStatus({ ...lecturers[0], first_opened_at: null }), "Pending access");
  assert.equal(contracts.recipientStatus({ ...lecturers[0], first_opened_at: "2030-01-01T00:00:00Z" }), "Opened");
  assert.equal(contracts.recipientStatus({ ...lecturers[1], first_opened_at: null }), "Account required");
  assert.equal(contracts.recipientStatus({ ...lecturers[0], link_status: "AMBIGUOUS_LINK", first_opened_at: null }), "Account link needs review");
});

test("deadline conversion rejects invalid calendar dates and consistently uses Sri Lanka time", () => {
  for (const date of ["", "2030-02-30T10:00", "2030-13-01T10:00", "2030-01-01T24:10", "2030-01-01", "bad"]) assert.equal(contracts.deadlineIso(date),null,date);
  assert.equal(contracts.deadlineIso("2030-01-01T00:00"),"2029-12-31T18:30:00.000Z");
  assert.equal(contracts.localDeadline(new Date("2029-12-31T18:30:00Z")),"2030-01-01T00:00");
});

test("invalid audiences, removed selections, empty forms and nonfuture deadlines fail before send", () => {
  for (const [kind,selected,department,deadline] of [["SELECTED",[],"","2030-01-01T10:00"],
    ["SELECTED",[id],"","2030-01-01T10:00"],["SELECTED",[staff,staff],"","2030-01-01T10:00"],
    ["DEPARTMENT",[],other,"2030-01-01T10:00"],["ALL_ELIGIBLE",[],"","2020-01-01T10:00"]]) {
    assert.equal(contracts.prepareSend(template,lecturers,kind,selected,department,"DIRECT",deadline).valid,false);
  }
  assert.equal(contracts.prepareSend({ ...template, questions: [] },lecturers,"ALL_ELIGIBLE",[],"","DIRECT","2030-01-01T10:00").valid,false);
  assert.equal(contracts.prepareSend(template,[],"ALL_ELIGIBLE",[],"","DIRECT","2030-01-01T10:00").valid,false);
});

test("Server Action input validation rejects malformed runtime payloads", () => {
  assert.equal(contracts.validSendBody(body),true);
  for (const value of [null,{}, { ...body, institution_id: "invalid" }, { ...body, expected_revision: 0 },
    { ...body, deadline: "2030-01-01T12:00:00" }, { ...body, channel: "EMAIL" },
    { ...body, audience: { kind: "SELECTED", staff_ids: [staff,staff] } }, { ...body, audience: { kind: "DEPARTMENT", department_id: "bad" } }]) assert.equal(contracts.validSendBody(value),false);
});

test("real service adapter forwards session auth, idempotency key and tenant without exposing tokens to UI", async () => {
  const calls=[]; let session={ accessToken: "private-test-token" }, status=201;
  const originalFetch=globalThis.fetch;
  const redirect=(url)=>{ throw new Error(url); };
  globalThis.fetch=async (url,options)=>{ calls.push({ url,options }); return new Response(JSON.stringify(status===201?{ distribution: { id } }:{ error:{ code:"REVISION_CONFLICT",message:"Changed" } }),{ status }); };
  try {
    const adapter=load("src/lib/form-sending.ts",{ "server-only":{}, "@/auth":{ auth:async()=>session }, "next/navigation":{ redirect },
      "@/lib/service-api":{ serviceUrl:()=>"http://127.0.0.1:8005", serviceRequest:async (service,url,options)=>{ calls.push({ url,options }); return ok([]); } } });
    await adapter.getFormRecipients(institution);await adapter.getDistributions(institution,3);await adapter.getDistribution(institution,id);await adapter.getDistributionRecipients(institution,id,2);
    assert.deepEqual(calls[1].options.query,{ institution_id:institution,page:3,page_size:20 });
    assert.equal(calls[3].url,`/api/v1/teaching/form-distributions/${id}/recipients`);
    const result=await adapter.sendSavedForm(id,body,"same-key");assert.equal(result.ok,true);
    const call=calls.at(-1);assert.equal(call.url,`http://127.0.0.1:8005/api/v1/teaching/form-templates/${id}/send`);
    assert.equal(call.options.headers["Idempotency-Key"],"same-key");assert.equal(call.options.headers.Authorization,"Bearer private-test-token");
    assert.deepEqual(JSON.parse(call.options.body),body);assert.equal(call.options.cache,"no-store");assert.equal(call.options.redirect,"error");
    status=409;assert.equal((await adapter.sendSavedForm(id,body,"same-key")).error.code,"REVISION_CONFLICT");
    status=401;await assert.rejects(adapter.sendSavedForm(id,body,"same-key"),/SessionExpired/);
    session=null;await assert.rejects(adapter.sendSavedForm(id,body,"same-key"),/SessionExpired/);
    session={ accessToken:"private-test-token" };globalThis.fetch=async()=>{ throw new Error("offline"); };
    assert.equal((await adapter.sendSavedForm(id,body,"same-key")).error.status,0);
  } finally { globalThis.fetch=originalFetch; }
});

test("sending actions enforce active Academic Admin institution and keep 409 errors for review", async () => {
  let context={ institutions:[{ id:institution,status:"ACTIVE" },{ id:other,status:"DISABLED" }] }, result=failure(409,"REVISION_CONFLICT");
  const calls=[],invalidated=[];
  const actions=load("src/lib/form-sending-actions.ts",{ "next/cache":{ revalidatePath:(...args)=>invalidated.push(args) },
    "@/lib/academic":{ getAcademicContext:async()=>context }, "@/lib/identity-records":{ isUuid:(s)=>typeof s==="string"&&/^[0-9a-f-]{36}$/.test(s) },
    "@/lib/form-sending-contracts":contracts,"@/lib/form-sending":{ getFormRecipients:async (...args)=>{ calls.push(args);return ok(lecturers); },sendSavedForm:async(...args)=>{ calls.push(args);return result; } } });
  assert.equal((await actions.sendFormDraft(id,{ ...body,institution_id:other },"key")).error.status,403);
  assert.equal((await actions.sendFormDraft(id,{},"key")).error.status,422);
  assert.equal((await actions.sendFormDraft(id,body,"bad key")).error.status,422);assert.equal(calls.length,0);
  assert.deepEqual(await actions.sendFormDraft(id,body,"same-key"),result);assert.equal(invalidated.length,0);
  result=ok({ distribution:{ id } });await actions.sendFormDraft(id,body,"same-key");assert.equal(invalidated.length,1);
  await actions.refreshEligibleLecturers(institution);context=null;
  const before=calls.length;assert.equal((await actions.refreshEligibleLecturers(institution)).error.status,403);assert.equal(calls.length,before);
});

function nodes(value) {
  if (Array.isArray(value)) return value.flatMap(nodes);
  if (!value || typeof value!=="object" || !value.props) return [];
  return [value,...nodes(value.props.children),...nodes(value.props.actions)];
}
const text=(node)=>typeof node==="string"?node:Array.isArray(node)?node.map(text).join(""):node?.props?text(node.props.children):"";
const primitives={ Card:({ children })=>React.createElement("section",{},children),EmptyState:({ title,children })=>React.createElement("div",{},title,children),
  Badge:({ children })=>React.createElement("span",{},children),PageHeader:({ title,actions })=>React.createElement("header",{},title,actions),formatDateTime:(s)=>s };
const styles=load("src/app/(academic)/academic/forms/_components/styles.ts");
function hookHarness() {
  const states=[],refs=[],work=[];let index=0,refIndex=0;
  return { work, reset:()=>{ index=0;refIndex=0; },react:{
    useState:(initial)=>{ const i=index++;if (!(i in states)) states[i]=typeof initial==="function"?initial():initial;return [states[i],(next)=>{ states[i]=typeof next==="function"?next(states[i]):next; }]; },
    useRef:(initial)=>{ const i=refIndex++;refs[i]??={ current:initial };return refs[i]; },useTransition:()=>[false,(fn)=>work.push(fn())],
  } };
}

test("send UI preserves selections and idempotency key after uncertain response and blocks rapid duplicate clicks", async () => {
  const h=hookHarness(),requests=[];let response=failure(0);
  const component=load("src/app/(academic)/academic/forms/_components/send-form.tsx",{ "react/jsx-runtime":jsxRuntime,react:h.react,"next/link":{ default:"a" },
    "@/components/console/ui":primitives,"@/lib/form-template-contracts":draftContracts,"@/lib/form-sending-contracts":contracts,"./styles":styles,
    "./copy-form-link":{ CopyFormLink:()=>null },"./form-preview":{ FormPreview:()=>null },"@/lib/form-template-actions":{ reloadDraftForm:async()=>ok({ ...template,revision:5 }) },
    "@/lib/form-sending-actions":{ refreshEligibleLecturers:async()=>ok(lecturers),sendFormDraft:async(...args)=>{ requests.push(args);return response; } } });
  const render=()=>{ h.reset();return component.SendForm({ initial:template,directory:ok(lecturers),institutionName:"Institution" }); };
  let tree=render();nodes(tree).find((n)=>n.type==="input"&&n.props.type==="checkbox").props.onChange({ target:{ checked:true } });
  tree=render();nodes(tree).find((n)=>n.type==="input"&&n.props.type==="datetime-local").props.onChange({ target:{ value:"2030-01-01T17:30" } });
  tree=render();const submit=nodes(tree).find((n)=>n.type==="form").props.onSubmit;
  submit({ preventDefault(){} });submit({ preventDefault(){} });await h.work.pop();assert.equal(requests.length,1);
  tree=render();assert.equal(nodes(tree).find((n)=>n.type==="input"&&n.props.type==="checkbox").props.checked,true);
  assert.equal(nodes(tree).find((n)=>n.type==="input"&&n.props.type==="datetime-local").props.value,"2030-01-01T17:30");
  nodes(tree).find((n)=>n.type==="form").props.onSubmit({ preventDefault(){} });await h.work.pop();assert.equal(requests[1][2],requests[0][2]);
  response=failure(409,"REVISION_CONFLICT");nodes(render()).find((n)=>n.type==="form").props.onSubmit({ preventDefault(){} });await h.work.pop();
  tree=render();assert.equal(nodes(tree).find((n)=>n.type==="button"&&n.props.type==="submit").props.disabled,true);
  nodes(tree).find((n)=>n.type==="button"&&text(n)==="Load latest saved draft").props.onClick();await h.work.pop();
  tree=render();assert.equal(nodes(tree).find((n)=>n.type==="input"&&n.props.type==="checkbox").props.checked,true);
  response=ok({ distribution:{ id,recipient_count:1,unlinked_count:0,ambiguous_count:0,deadline:body.deadline },share_url:"http://localhost:3000/forms/example",status:"OPEN" });
  nodes(tree).find((n)=>n.type==="form").props.onSubmit({ preventDefault(){} });await h.work.pop();
  assert.equal(requests.at(-1)[1].expected_revision,5);assert.notEqual(requests.at(-1)[2],requests[0][2]);
  tree=render();assert.ok(!nodes(tree).some((n)=>n.type==="form"));assert.ok(text(tree).includes("Linked recipients are pending access"));
});

test("copy link uses backend URL and offers manual copying when clipboard access fails", async () => {
  const h=hookHarness(),copied=[];const original=Object.getOwnPropertyDescriptor(globalThis,"navigator");
  Object.defineProperty(globalThis,"navigator",{ configurable:true,value:{ clipboard:{ writeText:async(url)=>copied.push(url) } } });
  try {
    const component=load("src/app/(academic)/academic/forms/_components/copy-form-link.tsx",{ "react/jsx-runtime":jsxRuntime,react:h.react,"./styles":styles });
    const render=()=>{ h.reset();return component.CopyFormLink({ url:"http://localhost:3000/forms/from-backend" }); };
    await nodes(render()).find((n)=>n.type==="button").props.onClick();assert.deepEqual(copied,["http://localhost:3000/forms/from-backend"]);assert.ok(text(render()).includes("Link copied."));
    globalThis.navigator.clipboard.writeText=async()=>{ throw new Error("Denied"); };
    await nodes(render()).find((n)=>n.type==="button").props.onClick();assert.ok(text(render()).includes("copy it manually"));
    assert.equal(nodes(render()).find((n)=>n.type==="input").props.readOnly,true);
  } finally { if (original) Object.defineProperty(globalThis,"navigator",original);else delete globalThis.navigator; }
});

test("send screen renders eligibility warnings and does not claim submission support", () => {
  const component=load("src/app/(academic)/academic/forms/_components/send-form.tsx",{ "react/jsx-runtime":jsxRuntime,react:React,"next/link":{ default:"a" },
    "@/components/console/ui":primitives,"@/lib/form-template-contracts":draftContracts,"@/lib/form-sending-contracts":contracts,"./styles":styles,
    "./copy-form-link":{ CopyFormLink:()=>null },"./form-preview":{ FormPreview:()=>null },"@/lib/form-template-actions":{},"@/lib/form-sending-actions":{} });
  const html=renderToStaticMarkup(React.createElement(component.SendForm,{ initial:template,directory:ok(lecturers),institutionName:"Institution" }));
  for (const phrase of ["Account required","All eligible lecturers","A department","Create a shareable link","UTC+05:30","submissions are not available yet"]) assert.ok(html.includes(phrase),phrase);
  assert.ok(!html.includes("Submitted"));assert.ok(!html.includes("private-test-token"));
});

test("sent tracking page renders backend status, paginated recipient access and institution restrictions", async () => {
  const reads=[];
  const page=load("src/app/(academic)/academic/forms/sent/[distributionId]/page.tsx",{
    "react/jsx-runtime":jsxRuntime,react:React,"next/link":{ default:"a" },
    "next/navigation":{ redirect:()=>{ throw new Error("redirect"); },notFound:()=>{ throw new Error("not-found"); } },
    "@/components/console/ui":{ ...primitives,first:(s)=>Array.isArray(s)?s[0]:s,pageNumber:(s)=>Number(s)||1,Pagination:()=>null,TableSkeleton:()=>null,LoadError:()=>null },
    "@/lib/academic":{ getAcademicContext:async()=>({ institutions:[{ id:institution,status:"ACTIVE",name:"Institution" }],primary:{ id:institution,status:"ACTIVE",name:"Institution" } }) },
    "@/lib/form-sending-contracts":contracts,"@/lib/identity-records":{ isUuid:()=>true },"../../_components/styles":styles,
    "../../_components/copy-form-link":{ CopyFormLink:({ url })=>React.createElement("input",{ value:url,readOnly:true }) },
    "@/lib/form-sending":{ getDistribution:async(...args)=>{ reads.push(args);return ok({ status:"CLOSED",share_url:"http://localhost:3000/forms/copy-me",distribution:{ frozen_template:template,deadline:body.deadline,recipient_count:3,created_at:body.deadline,unlinked_count:1,ambiguous_count:0 } }); },
      getDistributionRecipients:async(...args)=>{ reads.push(args);return ok({ items:[{ ...lecturers[0],first_opened_at:null },{ ...lecturers[0],staff_id:other,first_opened_at:body.deadline },{ ...lecturers[1],first_opened_at:null }],total:23,page:2,page_size:20 }); } },
  });
  const props={ params:Promise.resolve({ distributionId:id }),searchParams:Promise.resolve({ institution,page:"2" }) };
  const outer=page.default(props);const tree=await outer.props.children.type(outer.props.children.props);
  const html=renderToStaticMarkup(tree);
  for (const label of ["Sent","Closed","Pending access","Opened","Account required","copy-me"]) assert.ok(html.includes(label),label);
  assert.ok(!html.includes("Submitted"));assert.deepEqual(reads,[[institution,id],[institution,id,2]]);
  const bad=page.default({ ...props,searchParams:Promise.resolve({ institution:other }) });
  await assert.rejects(bad.props.children.type(bad.props.children.props),/not-found/);assert.equal(reads.length,2);
});
