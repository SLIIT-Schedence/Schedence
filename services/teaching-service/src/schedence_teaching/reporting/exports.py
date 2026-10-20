"""Private PDF/CSV projections; no database, assignments or policy mutations."""
import csv
from html import escape
from io import BytesIO, StringIO


CSV_COLUMNS={
    'summary':('staff_id','staff_name','staff_type','academic_rank','appointment','lecture_minutes','tutorial_minutes','workshop_minutes','lab_minutes',
        'assigned_teaching_minutes','online_minutes','physical_minutes','weekday_minutes','weekend_minutes','teaching_day_count',
        'full_version_teaching_minutes','weekly_teaching_target_minutes','difference_from_target_minutes','target_status',
        'weekly_teaching_cap_minutes','weekly_cap_excess_minutes','administrative_allocation_minutes',
        'weekly_approved_admin_reservation_minutes','weekly_proposed_admin_reservation_minutes','timed_reserved_admin_minutes',
        'timed_reserved_research_minutes','verified_completed_admin_minutes','recognized_work_ledger_complete',
        'current_policy_status','current_assignment_issue_codes','weekly_teaching_day_limit','teaching_day_limit_violated',
        'assigned_module_offering_count','assigned_module_offering_ids','assigned_lab_module_offering_count',
        'assigned_lab_delivery_count','full_version_module_offering_count','full_version_lab_module_offering_count','module_count_basis'),
    'assignments':('staff_id','staff_name','delivery_reference','date','start_minute','end_minute','participation_minutes',
        'module_codes','module_names','offering_ids','session_type','stream','delivery_mode','venue_id','group_ids','participation_evidence'),
    'targets':('staff_id','staff_name','academic_rank','appointment','full_version_teaching_minutes','weekly_teaching_target_minutes',
        'difference_from_target_minutes','target_shortfall_minutes','target_excess_minutes','target_status','target_comparison_scope',
        'weekly_teaching_cap_minutes','weekly_cap_excess_minutes','daily_cap_violations','policy_reference','classification_evidence'),
    'fairness':('staff_id','staff_name','academic_rank','appointment','full_version_teaching_minutes','weekly_teaching_target_minutes',
        'normalized_utilization','target_status','fairness_interpretation'),
}


def workload_csv(report,kind='summary'):
    """Keep integer minutes exact and version provenance on every record."""
    if kind not in CSV_COLUMNS:raise ValueError('Choose summary, assignments, targets or fairness CSV.')
    rows=report['assignments'] if kind=='assignments' else report['staff']
    output=StringIO(newline='')
    metadata=('institution_id','version_id','calendar_label','week_reference','lifecycle_status','validation_passed')
    if report.get('resolved_policy_digest') is not None:
        metadata += ('resolved_policy_digest','institution_policy_revision')
    writer=csv.writer(output,lineterminator='\r\n')
    writer.writerow((*metadata,*CSV_COLUMNS[kind]))
    def cell(v):
        if isinstance(v,(list,dict)):
            import json
            v=json.dumps(v,ensure_ascii=False,separators=(',',':'))
        # Staff/module display strings are untrusted spreadsheet text. Numeric
        # negative deviations remain real numbers; never rewrite source inputs.
        if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')):return "'"+v
        return '' if v is None else v
    for original in rows:
        r=dict(original)
        if kind=='assignments':
            r.update(module_codes='; '.join(m['module_code'] for m in r['modules']),
                module_names='; '.join(m['module_name'] for m in r['modules']),offering_ids='; '.join(m['offering_id'] for m in r['modules']),
                group_ids='; '.join(r['group_ids']))
        if kind=='fairness':
            r.update(normalized_utilization=r['target_utilization']['value'] if r['target_utilization'] else None,
                fairness_interpretation='DESCRIPTIVE_ONLY_NOT_OPPORTUNITY_ADJUSTED_FAIRNESS')
        writer.writerow([cell(report[k]) for k in metadata]+[cell(r.get(k)) for k in CSV_COLUMNS[kind]])
    return output.getvalue().encode('utf-8-sig')


def workload_pdf(report,kind='summary'):
    """Landscape A3 with repeated headers, automatic row wrapping and page totals."""
    if kind not in {'summary','individual','detailed'}:raise ValueError('Choose summary, individual or detailed PDF.')
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A3,landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle,PageBreak,KeepTogether
    output=BytesIO();page=landscape(A3);navy=colors.HexColor('#143148');teal=colors.HexColor('#007D83')
    style=ParagraphStyle('cell',fontName='Helvetica',fontSize=9,leading=11,textColor=navy)
    small=ParagraphStyle('small',parent=style,fontSize=8.5,leading=11)
    title=ParagraphStyle('title',parent=style,fontName='Helvetica-Bold',fontSize=21,leading=26,spaceAfter=12)
    heading=ParagraphStyle('heading',parent=style,fontName='Helvetica-Bold',fontSize=13,leading=17,spaceBefore=12,spaceAfter=9)
    def p(v,s=style):return Paragraph(escape(str(v)).replace('\n','<br/>'),s)
    def hours(v):return 'Not supplied' if v is None else f'{v/60:.2f}'
    def clock(v):return f'{v//60:02}:{v%60:02}'
    def table(headers,rows,widths):
        data=[[p(h) for h in headers]]+[[p(v) for v in row] for row in rows]
        t=LongTable(data,colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E1EDF3')),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
            ('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.3,colors.HexColor('#CBD8E1')),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F7F9FB')]),
            ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        return t
    scope='; '.join(sorted({f"Year {s['academic_year']} / Semester {s['semester']} / {s['stream']} / {s['specialization']}" for s in report['scopes']}))
    provenance='; '.join(sorted({s['kind'] for s in report['sources']})) or 'No source provenance supplied'
    story=[p('Schedence Lecturer Workload - '+kind.title(),title),p('VALIDATED EXPERIMENTAL DRAFT - NOT OFFICIALLY PUBLISHED',heading),
        p(f"Timetable version: {report['version_id']} (version {report['version_number']}); status: {report['lifecycle_status']}; validation: PASSED",small),
        p(f"Calendar: {report['calendar_label']}; reference week: {report['week_start']} to {report['week_end']}; {report['timezone']}",small),
        p('Saved academic scope: '+scope,small),p('Filters: '+str({k:v for k,v in report['filters'].items() if v is not None and k not in {'version_id','institution_id'}}),small),
        p('Saved input provenance: '+provenance+'. This report does not replace or regenerate a timetable.',small),
        p('Weekly target/cap comparisons use the complete selected version. Administrative/research reservations are not completed work. Semester alternatives are not combined.',small),Spacer(1,10)]
    current=report.get('current_teaching_policy')
    story.insert(2,p('Report source: '+report.get('report_source','SELECTED_SAVED_VERSION')+
        '; current institution teaching-policy assessment: '+(current['current_policy_status'] if current else 'NOT ASSESSED - NO RESOLVED INSTITUTION ASSIGNMENT POLICY')+
        '. Stored validation refers to the original version policy; it is not current-policy approval.',small))
    totals=report['totals']
    story.append(p(f"Staff: {totals['staff_count']} ({totals['academic_staff_count']} academic, {totals['instructor_count']} instructors). "
        f"Zero assigned: {totals['zero_assigned_count']}. Assigned teaching: {totals['assigned_teaching_hours']:.2f} hours. "
        f"Actual deliveries: {totals['unique_actual_deliveries']}.",heading))
    width=page[0]-64
    if kind=='summary':
        headers=['Staff ID','Staff name','Rank / appointment','Lecture h','Tutorial h','Workshop h','Lab h','Assigned h','Target h','Gap h','Cap h','Days']
        widths=[83,205,145,66,66,71,59,75,72,69,72,width-983]
        data=[]
        for r in report['staff']:
            classification=(r['academic_rank'] or 'Unclassified')+((' / '+r['appointment']) if r['appointment'] else '')
            data.append([r['staff_id'],r['staff_name'],classification,*[hours(r[k]) for k in ('lecture_minutes','tutorial_minutes','workshop_minutes','lab_minutes','assigned_teaching_minutes','weekly_teaching_target_minutes','difference_from_target_minutes','weekly_teaching_cap_minutes')],r['teaching_day_count']])
        story.append(table(headers,data,widths))
        story.extend([Spacer(1,10),p('Gap = full-version assigned teaching minus weekly teaching target. A positive gap is not a hard-cap violation. Missing classifications/targets/caps remain missing.',small)])
    elif kind=='individual':
        for index,r in enumerate(report['staff']):
            if index:story.append(PageBreak())
            story.append(p(f"{r['staff_id']} - {r['staff_name']}",heading))
            metrics=[('Rank',r['academic_rank'] or 'Not supplied'),('Appointment',r['appointment'] or 'Not supplied'),
                ('Assigned teaching hours',hours(r['assigned_teaching_minutes'])),('Full-version teaching hours',hours(r['full_version_teaching_minutes'])),
                ('Weekly teaching target hours',hours(r['weekly_teaching_target_minutes'])),('Target status',r['target_status']),
                ('Full-version teaching minus target hours',hours(r['difference_from_target_minutes'])),
                ('Weekly maximum teaching cap hours',hours(r['weekly_teaching_cap_minutes'])),('Teaching days',r['teaching_day_count']),
                ('Daily maximum teaching cap hours',hours(r['daily_teaching_cap_minutes'])),
                ('Explicit hard-cap violation', 'YES' if r['hard_cap_violated'] else
                    ('No configured maximum caps' if r['weekly_teaching_cap_minutes'] is None and r['daily_teaching_cap_minutes'] is None else 'No')),
                ('Online / physical teaching hours',hours(r['online_minutes'])+' / '+hours(r['physical_minutes'])),
                ('Weekday / weekend-stream teaching hours',hours(r['weekday_minutes'])+' / '+hours(r['weekend_minutes'])),
                ('Administrative allocation hours (separate)',hours(r['administrative_allocation_minutes'])),
                ('Weekly approved / proposed admin reservations h',hours(r['weekly_approved_admin_reservation_minutes'])+' / '+hours(r['weekly_proposed_admin_reservation_minutes'])),
                ('Timed administrative / research reservation hours',hours(r['timed_reserved_admin_minutes'])+' / '+hours(r['timed_reserved_research_minutes'])),
                ('Verified completed administrative hours',hours(r['verified_completed_admin_minutes'])),
                ('Completed-work ledger', 'Complete evidence' if r['recognized_work_ledger_complete'] else 'Incomplete / not supplied'),
                ('Policy source',r['policy_reference'] or 'No explicit workload profile; no title inference')]
            story.append(table(['Metric','Value'],metrics,[width*.42,width*.58]))
            items=[a for a in report['assignments'] if a['staff_id']==r['staff_id']]
            story.append(p('Actual teaching assignments',heading))
            if items:
                story.append(table(['Date / time - '+r['staff_id'],'Module code and full name','Session / delivery / stream','Venue','Assigned h'],[
                    [a['date']+' '+clock(a['start_minute'])+'-'+clock(a['end_minute']),
                     '; '.join(m['module_code']+' '+m['module_name'] for m in a['modules']),
                     a['session_type']+' / '+a['delivery_mode']+' / '+a['stream'],a['venue_id'] or 'Online',hours(a['participation_minutes'])] for a in items],
                    [170,width-535,170,120,75]))
            else:story.append(p('No assigned teaching deliveries in the selected report scope.',small))
    else:
        story.append(p('Module-wise teaching workload',heading))
        story.append(table(['Staff ID / name','Offering / module code and full name','Deliveries','Assigned h'],[
            [r['staff_id']+' / '+r['staff_name'],r['module_code']+' '+r['module_name']+'\n'+r['offering_id'],r['delivery_count'],hours(r['assigned_minutes'])]
            for r in report['modules']],[230,width-375,65,80]))
        if not report['module_totals_additive']:story.append(p('Shared-content deliveries have multiple offering labels. Module totals are non-additive; staff totals count one actual delivery.',small))
        story.append(PageBreak());story.append(p('Actual saved teaching deliveries by assigned staff',heading))
        story.append(table(['Staff ID / name','Date / time','Module code and full name','Type / mode / stream','Venue','Participation h'],[
            [r['staff_id']+' / '+r['staff_name'],r['date']+' '+clock(r['start_minute'])+'-'+clock(r['end_minute']),
             '; '.join(m['module_code']+' '+m['module_name'] for m in r['modules']),r['session_type']+' / '+r['delivery_mode']+' / '+r['stream'],
             r['venue_id'] or 'Online',hours(r['participation_minutes'])] for r in report['assignments']],
            [205,155,width-705,150,110,85]))
    story.append(Spacer(1,10));story.append(p('Workload fairness',heading))
    story.append(p('Existing target-normalized distribution metrics are descriptive only. Staff with missing targets are excluded from normalization. Availability-adjusted fairness is not inferred.',small))
    story.append(table(['Rank / appointment','Staff','Mean utilization','Variance','Mean absolute deviation'],[
        [(r['academic_rank'] or 'Unclassified')+(' / '+r['appointment'] if r['appointment'] else ''),r['staff_count'],
         f"{r['mean_utilization']['value']:.3f}",f"{r['variance']['value']:.3f}",f"{r['mean_absolute_deviation']['value']:.3f}"]
        for r in report['fairness']['descriptive_comparisons']],[width*.36,width*.1,width*.18,width*.18,width*.18]))
    story.append(p('Unresolved academic mappings and Research requirements remain unchanged. No publication or completed-work claim is made.',small))
    def footer(c,doc):
        c.setFont('Helvetica',8);c.setFillColor(navy)
        c.drawString(32,23,'SCHEDENCE | VALIDATED DRAFT - NOT OFFICIALLY PUBLISHED | '+report['version_id'])
        c.drawRightString(page[0]-32,23,str(doc.page))
    doc=SimpleDocTemplate(output,pagesize=page,leftMargin=32,rightMargin=32,topMargin=30,bottomMargin=40,
        title='Schedence Lecturer Workload '+kind.title(),author='Schedence Teaching Timetable Backend')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()
