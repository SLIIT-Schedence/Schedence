import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";

// Presentation/read-adapter checks only. All service reads are mocked; no credentials or databases.
function load(file, mocks = {}) {
  const code = ts.transpileModule(readFileSync(path.join(import.meta.dirname, "..", file), "utf8"), {
    fileName:file,compilerOptions:{ target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX },
  }).outputText;
  const mod={ exports:{} };
  new Function("require","module","exports",code)((name)=>{ assert.ok(Object.hasOwn(mocks,name),`Unexpected dependency ${name}`);return mocks[name]; },mod,mod.exports);
  return mod.exports;
}
const helpers=load("src/lib/staff-detail.ts");
const institution="11111111-1111-4111-8111-111111111111",staffId="22222222-2222-4222-8222-222222222222";
const member={ id:staffId,institutionId:institution,staffNumber:"L001",fullName:"Academic Staff Member",academicRank:"Lecturer",email:"lecturer@example.test",identityUserId:null,employmentStatus:"ACTIVE",createdAt:"2026-01-01" };
const ok=(data)=>({ ok:true,data });
const coverage=(group)=>({ module_code:"IT101",module_name:"Computing Fundamentals",group_id:group,session_type:"LECTURE",semester:1 });
const session=(patch={})=>({ delivery_reference:"delivery-1",teaching_date:"2026-10-19",day:"MONDAY",start_minute:480,end_minute:600,start_time:"08:00",end_time:"10:00",delivery_mode:"PHYSICAL",lecturer:{ staff_id:"L001",staff_name:member.fullName },venue:{ venue_name:"Room A" },coverage:[coverage("Y1-A"),coverage("Y1-B")],...patch });
const workload=(hours)=>({ staff_id:"L001",staff_name:member.fullName,assigned_teaching_hours:hours,weekly_teaching_target_hours:10,difference_from_target_hours:hours-10,
  teaching_day_count:1,weekly_teaching_day_limit:5,target_status:"BELOW_TARGET",hard_cap_violated:false,teaching_day_limit_violated:false,weekly_teaching_cap_hours:15 });
const summary=(version)=>({ institution_id:institution,version_id:`version-${version}`,version_number:version,lifecycle_status:"DRAFT_VALIDATED",validation_status:"PASSED",scopes:[{ semester:1 }],
  calendar:{ week_start:"2026-10-19",week_end:"2026-10-25",timezone:"Asia/Colombo" } });
const version=(n=1)=>({ versionId:`version-${n}`,label:`Semester 1 · version ${n} · draft validated`,summary:summary(n),workload:workload(n*2),modules:[{ module_code:"IT101",module_name:"Computing Fundamentals",session_types:["LECTURE"],sessions:1,minutes:n*120 }],sessions:[session({ end_minute:480+n*120 })] });
const primitive={
  Card:({ title,description,actions,children })=>React.createElement("section",{},title&&React.createElement("h2",{},title),description&&React.createElement("p",{},description),actions,children),
  EmptyState:({ title,children })=>React.createElement("div",{},React.createElement("h3",{},title),children),
  Badge:({ children })=>React.createElement("span",{},children),
  Kpi:({ label,value,hint,href })=>React.createElement("article",{},React.createElement("h3",{},label),React.createElement("p",{},value),hint,href&&React.createElement("a",{ href },label)),
  formatDate:(s)=>s,first:(value)=>Array.isArray(value)?value[0]:value,
  LoadError:({ what,error,retryHref })=>React.createElement("div",{ role:"alert" },`${what}: ${error.message}`,React.createElement("a",{ href:retryHref },"Retry")),
  TableSkeleton:()=>null,PageSkeleton:()=>null,
};
const ui={ "react/jsx-runtime":jsxRuntime,"@/components/console/ui":primitive,"@/components/icon":{ Icon:()=>null },"@/lib/staff-detail":helpers,
  "@/lib/teaching":{ SESSION_TYPES:{ LECTURE:"Lecture",LAB:"Lab" } } };
const sessionsComponent=load("src/app/(academic)/academic/staff/[staffId]/_components/assigned-sessions.tsx",ui);
const charts=load("src/app/(academic)/academic/staff/[staffId]/_components/workload-charts.tsx",ui);

async function expand(value) {
  if (Array.isArray(value)) {
    const children = await Promise.all(value.map(expand));
    // Expanding Server Components turns JSX's static children into arrays for this harness.
    return children.map((child,index)=>React.isValidElement(child)?React.cloneElement(child,{ key:child.key??`expanded-${index}` }):child);
  }
  if (!React.isValidElement(value)) return value;
  if (typeof value.type==="function") return expand(await value.type(value.props));
  if (value.type===React.Suspense) return expand(value.props.children);
  return React.cloneElement(value,{},await expand(value.props.children));
}
async function html(value) { return renderToStaticMarkup(await expand(value)); }

function fixture() {
  const calls={ preview:[],sessions:[],reports:[],appointments:[],listed:[] };
  const data={ versions:[summary(1),summary(2)],failure:null };
  const teaching=load("src/app/(academic)/academic/staff/[staffId]/_components/teaching-sections.tsx",{
    ...ui,react:{ cache:(fn)=>fn },"@/lib/teaching":{ ...ui["@/lib/teaching"],
      listTimetables:async(id)=>{ calls.listed.push(id);return data.failure??ok({ items:data.versions }); },
      getWorkloadReport:async(id,selected)=>{ calls.reports.push([id,selected]);return ok({ staff:[workload(selected==="version-2"?4:2)] }); },
      allSessions:async(id,selected,filters)=>{ calls.sessions.push([id,selected,filters]);return ok([session({ end_minute:selected==="version-2"?720:600 })]); },
    },"@/lib/teaching-preview":{ HISTORICAL_PREVIEW_LABEL:"Historical Experimental Preview",
      modulesTaught:()=>version().modules,previewStaff:async(...args)=>{ calls.preview.push(args);return [{ source:{ run:"Archived run",institution_code:"TEST-INSTITUTION" },semesters:[1],versionId:"preview-id",workload:workload(99),modules:version().modules }]; } } });
  const academic={ getStaff:async(id)=>{ assert.equal(id,staffId);return ok(member); },getAcademicContext:async()=>({ institutions:[{ id:institution,name:"Staff Institution" }] }),
    getWorkload:async()=>ok({ asOf:"2026-10-21",aggregate:{ computed:false,reason:"Separate policies",rule:null,totalHoursPerWeek:null },appointments:[{ appointmentId:"appointment",positionName:"Coordinator",unitName:"Computing",hoursSource:"POLICY",hoursPerWeek:3 }] }),
    listAppointments:async(id,options)=>{ calls.appointments.push([id,options]);return ok([]); } };
  const page=load("src/app/(academic)/academic/staff/[staffId]/page.tsx",{
    ...ui,react:React,"next/link":{ default:(props)=>{ const dom={ ...props };delete dom.scroll;return React.createElement("a",dom); } },"next/navigation":{ notFound:()=>{ throw new Error("not-found"); } },"@/lib/academic":academic,
    "@/lib/identity-records":{ isUuid:(value)=>value===staffId },"./_components/teaching-sections":teaching,
    "./_components/assigned-sessions":sessionsComponent,"./_components/workload-charts":charts,
    "../../_components/academic-ui":{ ACADEMIC_SERVICE:"Academic",AppointmentBadge:primitive.Badge,CodeTag:primitive.Badge,EmploymentBadge:()=>"Active",StaffAvatar:()=>null,
      combinationRule:(s)=>s??"Separate policies",formatDay:(s)=>s,hours:(s)=>`${s} h`,period:()=>"Period",UNIT_TYPES:{} },
  });
  return { calls,data,teaching,render:async(query={})=>html(page.default({ params:Promise.resolve({ staffId }),searchParams:Promise.resolve(query) })) };
}

test("all five section links preserve the version and legacy appointment history URLs still work",()=>{
  assert.deepEqual(helpers.STAFF_TABS.map((tab)=>tab.label),["Overview","Assigned Sessions","Workload","Availability & Preferences","History"]);
  for (const tab of helpers.STAFF_TABS) assert.equal(new URL(helpers.staffHref(staffId,tab.id,"version-2"),"http://localhost").searchParams.get("version"),"version-2");
  assert.equal(helpers.staffTab(undefined,"1"),"history");assert.equal(helpers.staffTab("bad"),"overview");assert.equal(helpers.staffTab("sessions","1"),"sessions");
});

test("combined group coverage consumes one delivery duration and keeps all session fields",()=>{
  const facts=helpers.sessionFacts(session());
  assert.deepEqual(facts.groups,["Y1-A","Y1-B"]);assert.equal(facts.modules.length,1);assert.deepEqual(facts.types,["LECTURE"]);
  assert.equal(helpers.teachingLoadByDay([session()])[0].minutes,120);
  assert.equal(helpers.sessionFacts(session({ venue:null,delivery_mode:"ONLINE" })).venue,"Online");
  assert.equal(helpers.sessionFacts(session({ venue:null })).venue,"Not recorded");
});

test("sessions sort chronologically without mutating the backend array",()=>{
  const rows=[session({ delivery_reference:"late",start_minute:720 }),session({ delivery_reference:"early",start_minute:480 })];
  assert.equal(helpers.orderedSessions(rows)[0].delivery_reference,"early");assert.equal(rows[0].delivery_reference,"late");
});

test("assigned sessions have all eight table columns and a compact mobile layout",async()=>{
  const markup=await html(React.createElement(sessionsComponent.AssignedSessions,{ version:version(2) }));
  for (const label of ["Module","Session type","Student group","Day","Time","Venue","Semester","Version","Y1-A","Y1-B","08:00","10:00","Room A","v2"]) assert.ok(markup.includes(label),label);
  assert.ok(markup.includes("md:hidden"));assert.ok(markup.includes("overflow-x-auto"));
  assert.equal((markup.match(/<tr/g)??[]).length,2,"one header and one combined delivery row");
  const empty=await html(React.createElement(sessionsComponent.AssignedSessions,{ version:{ ...version(),sessions:[] } }));
  assert.ok(empty.includes("No assigned sessions"));
});

test("workload charts handle missing and zero targets without fabricated percentages",async()=>{
  assert.deepEqual(helpers.workloadScale(0,0),{ assigned:0,target:0 });
  assert.deepEqual(helpers.workloadScale(12,6),{ assigned:100,target:50 });
  assert.equal(helpers.workloadScale(5,null).target,null);
  const selected=version();selected.workload.weekly_teaching_target_hours=null;selected.workload.difference_from_target_hours=null;
  const markup=await html(React.createElement(charts.WorkloadCharts,{ version:selected }));
  assert.ok(markup.includes("Target not available"));assert.ok(markup.includes("Monday 2 h"));assert.ok(!markup.includes("NaN"));assert.ok(!markup.includes("Infinity"));
  assert.ok(markup.includes('role="img"'));assert.ok(markup.includes("Teaching load by day"));
});

test("teaching adapter keeps institution scope, lecturer filtering and validated-version eligibility",async()=>{
  const f=fixture();f.data.versions.push({ ...summary(3),validation_status:"FAILED" },{ ...summary(4),scopes:[] });
  const result=await f.teaching.getStaffTeaching(institution,"L001");
  assert.equal(result.status,"ok");assert.equal(result.versions.length,2);
  assert.deepEqual(f.calls.listed,[institution]);assert.deepEqual(f.calls.reports,[[institution,"version-1"],[institution,"version-2"]]);
  assert.deepEqual(f.calls.sessions,[[institution,"version-1",{ lecturer:"L001" }],[institution,"version-2",{ lecturer:"L001" }]]);
  assert.equal(result.versions[0].sessions.length,1);assert.equal(f.calls.preview.length,0);
});

test("overview shows summary and quick stats without loading historical previews",async()=>{
  const f=fixture();const markup=await f.render({ version:"version-2" });
  for (const label of [member.fullName,"Staff summary","Quick teaching statistics","Current appointments","Assigned modules","4 h"]) assert.ok(markup.includes(label),label);
  assert.equal(f.calls.preview.length,0);assert.deepEqual(f.calls.appointments,[[staffId,{ includeEnded:false }]]);
  assert.ok(!markup.includes("99 h"));assert.ok(!markup.includes("Archived run"));assert.ok(markup.includes("tab=sessions&amp;version=version-2"));
});

test("assigned sessions and workload tabs render only the selected version",async()=>{
  const f=fixture();const sessions=await f.render({ tab:"sessions",version:"version-2" });
  assert.ok(sessions.includes("Assigned sessions"));assert.ok(sessions.includes("<table"));assert.equal(f.calls.preview.length,0);
  const workloadHtml=await f.render({ tab:"workload",version:"version-2" });
  for (const label of ["Assigned hours vs target","Teaching load by day","Administrative workload","Separate policies","Monday 4 h"]) assert.ok(workloadHtml.includes(label),label);
  assert.ok(!workloadHtml.includes("99 h"));assert.equal(f.calls.preview.length,0);
});

test("history alone loads separate labelled preview records and ended appointments",async()=>{
  const f=fixture();const markup=await f.render({ history:"1",version:"version-2" });
  assert.deepEqual(f.calls.preview,[["L001",member.fullName]]);assert.deepEqual(f.calls.appointments,[[staffId,{ includeEnded:true }]]);
  for (const label of ["Historical experimental previews","TEST-INSTITUTION","Archived run","<details","99 h"]) assert.ok(markup.includes(label),label);
  assert.ok(markup.includes("not published"));
});

test("availability stays explicitly unavailable and read failures never become zero workload",async()=>{
  const f=fixture();const availability=await f.render({ tab:"availability" });
  assert.ok(availability.includes("Availability &amp; preferences"));assert.ok(availability.includes("not available in this view"));assert.equal(f.calls.preview.length,0);
  f.data.failure={ ok:false,error:{ status:403,code:"FORBIDDEN",message:"Teaching access denied" } };
  const error=await f.render({ tab:"sessions",version:"version-2" });
  assert.ok(error.includes("Teaching access denied"));assert.ok(error.includes("Not available"));
  assert.ok(error.includes("tab=sessions&amp;version=version-2"));
  f.data.failure=null;f.data.versions=[];
  const empty=await f.render({ tab:"workload" });assert.ok(empty.includes("No validated teaching timetable"));assert.ok(empty.includes("Administrative workload"));
});
