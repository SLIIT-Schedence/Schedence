"""Private, standalone weekly resource views of independently validated bookings."""

from html import escape
import json


def weekly_html(batch, rows, views):
    options = []
    for kind in ("group", "lecturer", "venue"):
        entries = []
        for identity, occurrences in views[kind].items():
            if not occurrences:
                continue
            caption = identity
            if kind == "group":
                caption = batch.group_specializations[identity] + " / " + identity
            entries.append(f'<option value="{escape(kind + ":" + identity, quote=True)}">{escape(caption)}</option>')
        options.append(f'<optgroup label="{kind.title()}">{"".join(entries)}</optgroup>')
    payload = dict(rows=rows, views={kind+":"+identity: occurrences for kind in ("group", "lecturer", "venue")
        for identity, occurrences in views[kind].items() if occurrences},
        dates={row.day: batch.configuration.calendar.day_dates[row.day].isoformat() for row in batch.config.days},
        start=min(window.start_minute for day in batch.config.days for spans in day.windows.values() for window in spans),
        end=max(window.end_minute for day in batch.config.days for spans in day.windows.values() for window in spans),
        lunches={str(row.academic_year): [row.window.start_minute, row.window.end_minute]
                 for row in batch.solver_inputs["student_lunch_policy"].year_periods},
        groups={row.group_id: dict(academic_year=row.academic_year, stream=row.stream.value) for row in batch.plan.groups},
        windows={day.day: {stream.value: [[window.start_minute, window.end_minute] for window in spans]
                          for stream, spans in day.windows.items()} for day in batch.config.days},
        lunch_dates=sorted(day.isoformat() for day in batch.solver_inputs["student_lunch_policy"].applicable_dates),
        lunch_streams=sorted(row.value for row in batch.solver_inputs["student_lunch_policy"].applicable_streams))
    # Source text cannot escape the script element.
    encoded = json.dumps(payload).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    scope_notice = f'{len(rows)} scheduled occurrences in the exported input scope. '
    if batch.metadata.get("research_pending_cohort_count"):
        scope_notice += 'ELECTIVE / KNOWN TEACHING COMPONENT ONLY. Compulsory Research is pending session mapping; this is not a complete Year 4 academic timetable. '
    if batch.metadata.get("out_of_scope_occurrences") is not None:
        scope_notice += f'{batch.metadata["out_of_scope_occurrences"]} other global occurrences are outside this request.'
    elif "selected_complete_scopes" in batch.metadata:
        scope_notice += 'Other cohorts are outside this request; their corrected enrollment demand is not counted here.'
    week_caption = f'{batch.configuration.calendar.week_start.isoformat()} to {batch.configuration.calendar.week_end.isoformat()}'
    source_caption = ("SYNTHETIC FIXTURE: modules, staff IDs, venues and test groups" if batch.metadata.get("source_academic_status", "").startswith("SYNTHETIC") else
        "Real source modules, staff IDs and venues / approved proportional test groups")
    semester_notice = ("Only the selected semester is included; separate semester previews are alternatives."
        if len(batch.metadata.get("semesters", ()))==1 else "Semester cohorts are concurrent within this selected scope.")
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PP1 complete-scope weekly timetable</title><style>
body{font:14px/1.5 system-ui,sans-serif;margin:24px;background:#f3f6f9;color:#17324d}main{max-width:1550px;margin:auto}
header{background:#17324d;color:white;padding:24px;border-radius:12px}h1{margin:8px 0}.label{background:#ffd27b;color:#332500;padding:4px 8px;font-weight:800}
select{width:100%;padding:12px;margin:12px 0;font:inherit}a{color:#175b91}.notice{background:#fff0bc;padding:14px}
.scroll{overflow:auto}.week{display:grid;grid-template-columns:64px repeat(7,minmax(170px,1fr));min-width:1254px;background:white;border:1px solid #cbd5df}
.head{padding:10px 5px;background:#e5edf4;font-weight:700;text-align:center}.times,.day{height:720px;position:relative;border-right:1px solid #cbd5df;background:repeating-linear-gradient(to bottom,transparent 0,transparent 39px,#e6edf3 39px,#e6edf3 40px)}
.tick{position:absolute;font-size:12px;left:7px}.event{position:absolute;box-sizing:border-box;left:4px;right:4px;border:1px solid #8cb4d4;border-left:4px solid #226fa6;border-radius:5px;background:#eaf4fc;padding:5px;overflow:auto;font-size:12px;line-height:1.4}
.event.LAB{background:#e9f5ef;border-color:#9ac7b1;border-left-color:#267a51}.event.TUTORIAL{background:#f6edff;border-color:#c6a6df;border-left-color:#8752af}.event.WORKSHOP{background:#fff4df;border-color:#d9bc84;border-left-color:#a06a16}
.lunch{position:absolute;left:0;right:0;background:#fff0bc;box-sizing:border-box;padding:8px;color:#6d5316;font-size:12px}
</style></head><body><main><header><span class="label">''' + escape(batch.configuration.output_label) + '''</span><h1>Complete-scope weekly timetable</h1><p>''' + escape(source_caption) + ''' / configurable PP1 reference week</p></header>
<p>Configured reference week: ''' + escape(week_caption) + '''.</p><p><a href="timetable.html">Scope result, specialization views and validation</a> · <a href="timetable.csv">Scheduled CSV</a> · <a href="timetable.json">Scheduled JSON</a></p>
<p class="notice">''' + escape(scope_notice) + ''' This is a complete timetable for the explicitly selected scope only. ''' + escape(semester_notice) + ''' Separate preview runs must not be combined. Student lunch applies to student groups; no lecturer lunch rule is inferred. Dates, windows and lunch applicability come from the exported run configuration.</p>
<label for="resource"><strong>Student group, lecturer or venue</strong></label><select id="resource">''' + ''.join(options) + '''</select><p id="count"></p><div class="scroll"><div class="week" id="week"></div></div>
<p>Each card is one actual booking. A leaf-group view includes ancestor teaching and its own labs; sibling labs are excluded. Lecturer and venue views count each booking once.</p></main>
<script>const data=''' + encoded + ''';
const clock=n=>String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0');
const text=(tag,value,cls)=>{const n=document.createElement(tag);n.textContent=value;if(cls)n.className=cls;return n;};
function render(){const chosen=document.getElementById('resource').value;const ids=new Set(data.views[chosen]);const rows=data.rows.filter(r=>ids.has(r.occurrence_id));
const root=document.getElementById('week');root.style.gridTemplateColumns='64px repeat('+Object.keys(data.dates).length+',minmax(170px,1fr))';root.replaceChildren();root.append(text('div','Time','head'));
for(const [day,date] of Object.entries(data.dates))root.append(text('div',day.slice(0,3)+' '+date.slice(5),'head'));
const times=text('div','','times');times.style.height=((data.end-data.start)*4/3)+'px';for(let m=data.start;m<data.end;m+=30){const t=text('div',clock(m),'tick');t.style.top=((m-data.start)*4/3)+'px';times.append(t);}root.append(times);
for(const day of Object.keys(data.dates)){const col=text('div','','day');col.style.height=((data.end-data.start)*4/3)+'px';const daily=rows.filter(r=>r.day===day);
if(chosen.startsWith('group:')){const group=data.groups[chosen.slice(6)];const [start,end]=data.lunches[group.academic_year];const windows=data.windows[day][group.stream]||[];
if(data.lunch_dates.includes(data.dates[day])&&data.lunch_streams.includes(group.stream)&&windows.some(([a,b])=>a<end&&start<b)){const lunch=text('div',clock(start)+'–'+clock(end)+' Student lunch','lunch');lunch.style.top=((Math.max(start,data.start)-data.start)*4/3)+'px';lunch.style.height=((Math.min(end,data.end)-Math.max(start,data.start))*4/3)+'px';col.append(lunch);}}
for(const row of daily){const minutes=s=>Number(s.slice(0,2))*60+Number(s.slice(3));const event=text('div','','event '+row.session_type);event.style.top=((minutes(row.start_time)-data.start)*4/3)+'px';event.style.height=((minutes(row.end_time)-minutes(row.start_time))*4/3)+'px';
event.append(text('strong',row.start_time+'–'+row.end_time+' '+row.module_code),text('div',row.module_name),text('div',row.session_type+' · '+row.specialization+' Y'+row.academic_year+' S'+row.semester),text('div',row.lecturer_id+' · '+(row.delivery_mode==='ONLINE'?'ONLINE (no physical venue)':row.venue_id)));event.title=row.group_id+' / '+row.occurrence_id;col.append(event);}root.append(col);}
document.getElementById('count').textContent=rows.length+' actual attended/assigned bookings; '+rows.reduce((s,r)=>s+r.duration_minutes,0)/60+' contact hours in this view.';}
document.getElementById('resource').addEventListener('change',render);render();</script></body></html>'''
