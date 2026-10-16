"""Optional PDF presentation over independently validated native bookings.

Install the teaching service's ``pdf`` extra to use this offline exporter.
One logical timetable belongs to a main group, including all descendants.
"""

from collections import defaultdict
from dataclasses import dataclass
from html import escape
from pathlib import Path

from schedence_teaching.solver.evaluation import validate_evaluation_result

from .group_timetables import booking_groups, booking_identity, main_group_timetables, parallel_booking_lanes


@dataclass(frozen=True)
class PdfExportReport:
    pages: int
    main_group_views: int
    unique_deliveries: int
    displayed_deliveries: int
    continuation_pages: int
    manifest: tuple[dict, ...]


def export_main_group_pdf(batch, result, destination, *, academic_year, semester, streams, track_labels=None):
    """Fail before authoring on corrupt/partial scheduling results.

    Parallel sibling sessions get separate lanes. If a full week's lanes cannot
    fit at the fixed font size, the same main-group timetable continues over day
    panels; it never turns into individual subgroup timetable pages.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Paragraph

    validate_evaluation_result(batch.prepared, batch.config, result, **batch.solver_inputs)
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError('Preserve earlier PDF exports; choose a new destination.')
    group_rows = [g for g in batch.plan.groups if g.academic_year == academic_year and g.semester == semester and g.stream.value in streams]
    if not group_rows:
        raise ValueError('The selected PDF period/stream has no student groups.')
    groups = {g.group_id: g for g in group_rows}
    selected = tuple(b for b in result.schedule if set(booking_groups(b)) & groups.keys())
    if any(set(booking_groups(b)) - groups.keys() for b in selected):
        raise ValueError('Cross-scope combined attendance needs an explicit complete export scope.')
    tracks = batch.group_specializations
    labels = track_labels or {}
    W, H = landscape(A3)
    margin, time_w, top, bottom, header_h = 32, 74, H-150, 70, 29
    navy, teal, ink, muted = (colors.HexColor(s) for s in ('#143148','#007D83','#19374D','#526778'))
    grid = colors.HexColor('#D6E0E8')
    fills = dict(LECTURE='#E3F1FA', TUTORIAL='#E8F4EA', LAB='#EFE8F8', WORKSHOP='#FBECDD')
    accents = dict(LECTURE='#4F8BAC', TUTORIAL='#50875B', LAB='#8059A1', WORKSHOP='#BE863F')
    regular, bold = 'Helvetica', 'Helvetica-Bold'
    arial = Path('C:/Windows/Fonts/arial.ttf')
    if arial.is_file():
        regular, bold = 'SchedencePDF', 'SchedencePDF-Bold'
        pdfmetrics.registerFont(TTFont(regular, str(arial)))
        pdfmetrics.registerFont(TTFont(bold, str(arial.with_name('arialbd.ttf'))))
        pdfmetrics.registerFontFamily(regular, normal=regular, bold=bold)
    style = ParagraphStyle('session', fontName=regular, fontSize=9.2, leading=9.8, textColor=ink)
    modules = {m.offering_id:m for m in batch.prepared.input_snapshot.module_offerings}
    staff = {s.staff_id:s.staff_name for s in batch.prepared.input_snapshot.staff_members}
    venues = {v.venue_id:v.venue_name for v in batch.solver_inputs['venue_input'].venues}
    calendar = batch.configuration.calendar
    by_scope = defaultdict(list)
    for g in group_rows:
        by_scope[g.stream.value, tracks[g.group_id]].append(g)
    views = []
    for scope in sorted(by_scope, key=lambda k: (['WEEKDAY','WEEKEND'].index(k[0]), k[1])):
        scope_groups = by_scope[scope]
        ids = {g.group_id for g in scope_groups}
        scope_bookings = [b for b in selected if ids.intersection(booking_groups(b))]
        views.extend((scope, view) for view in main_group_timetables(scope_groups,scope_bookings))

    def clock(value):
        return f'{value//60:02}:{value%60:02}'

    def paragraph_for(booking, view):
        coverage = booking.covered_sessions if hasattr(booking, 'covered_sessions') else (booking.session,)
        codes = ', '.join(dict.fromkeys(modules[s.offering_id].module_code for s in coverage))
        names = ' / '.join(dict.fromkeys(modules[s.offering_id].module_name for s in coverage))
        kind = coverage[0].session_type.value
        mode = getattr(booking,'delivery_mode',None)
        mode = mode.value if mode else 'PHYSICAL'
        audience = ', '.join(view.labels[g] for g in booking_groups(booking) if g in view.labels)
        venue = 'Online - no physical venue' if mode == 'ONLINE' else venues[booking.assigned_venue_id]
        if mode != 'ONLINE' and venue != booking.assigned_venue_id:
            venue += f' ({booking.assigned_venue_id})'
        lines = [f'<b>{escape(codes)}</b> | {clock(booking.start_minute)}-{clock(booking.end_minute)}',
            f'<b>{escape(names)}</b>', f'{kind.title()} | {mode.title()} | {escape(audience)}',
            f'{escape(staff[booking.assigned_staff_id])} ({escape(booking.assigned_staff_id)})',escape(venue)]
        return Paragraph('<br/>'.join(lines),style), kind

    day_modes = {(d.stream.value,d.day):d.mode.value for d in batch.config.delivery_calendar.days}
    def days_for(stream):
        return sorted([dict(day=d.day,date=calendar.day_dates[d.day],windows=d.windows[next(s for s in d.windows if s.value==stream)],
            mode=day_modes[stream,d.day]) for d in batch.config.days if any(s.value==stream for s in d.windows)],key=lambda d:d['date'])

    def layout(scope,view):
        days = days_for(scope[0])
        start = min(w.start_minute for d in days for w in d['windows'])
        end = max(w.end_minute for d in days for w in d['windows'])
        height = top-bottom
        lanes = parallel_booking_lanes(view.bookings)
        def fits(panels):
            for panel in panels:
                width = (W-2*margin-time_w)/len(panel)
                names = {d['day'] for d in panel}
                for booking,lane,count in lanes:
                    if booking.day not in names: continue
                    p,_ = paragraph_for(booking,view)
                    _,h=p.wrap(width/count-18,H)
                    available=(booking.end_minute-booking.start_minute)/(end-start)*height-10
                    if h>available: return False
            return True
        panels = [days]
        if not fits(panels):
            panels=[days[i:i+3] for i in range(0,len(days),3)]
        if not fits(panels):
            panels=[days[i:i+2] for i in range(0,len(days),2)]
        if not fits(panels):
            panels=[[d] for d in days]
        if not fits(panels):
            raise ValueError('A main-group day cannot fit legibly; choose a reviewed larger layout.')
        return panels,start,end,lanes

    layouts=[(scope,view,*layout(scope,view)) for scope,view in views]
    page_count=sum(len(panels) for _,_,panels,_,_,_ in layouts)
    destination.parent.mkdir(parents=True,exist_ok=True)
    c=canvas.Canvas(str(destination),pagesize=(W,H),pageCompression=1)
    c.setTitle(f'Schedence Year {academic_year} Semester {semester} - corrected main-group timetables')
    c.setAuthor('Schedence Teaching Timetable Backend')
    c.setSubject('Validated local PP1 experimental draft; source-preserving academic-track correction; not officially published.')
    manifest=[]
    page=0
    def text(x,y,value,size=10,color=ink,is_bold=False):
        c.setFillColor(color);c.setFont(bold if is_bold else regular,size);c.drawString(x,y,str(value))
    lunch_by_year={p.academic_year:p.window for p in batch.solver_inputs['student_lunch_policy'].year_periods}
    lunch=lunch_by_year[academic_year]
    for scope,view,panels,start,end,lanes in layouts:
        stream,track=scope
        main=groups[view.main_group_id]
        for part,panel in enumerate(panels,1):
            page+=1
            c.bookmarkPage(f'page-{page}')
            c.addOutlineEntry(f'{stream.title()} / {labels.get(track,track)} / {view.labels[main.group_id]}' +
                (f' - continuation {part}' if part>1 else ''), f'page-{page}',level=0)
            text(margin,H-25,'SCHEDENCE | VALIDATED PP1 EXPERIMENTAL DRAFT | NOT OFFICIALLY PUBLISHED',10,teal,True)
            text(margin,H-56,f'Year {academic_year} - Semester {semester}',24,navy,True)
            text(margin,H-79,f'Academic track: {labels.get(track,track)}   |   Stream: {stream.title()}   |   Main Group: {view.labels[main.group_id]} ({main.student_count} students)',11.5,navy,True)
            children=', '.join(f'{view.labels[g]} ({groups[g].student_count})' for g in view.subgroup_ids)
            text(margin,H-96,'Subgroups: '+children+' | Counts are approved PP1 estimates.',9.5,muted)
            text(margin,H-111,'Source main-group ID: '+main.group_id,7.4,muted)
            c.setFillColor(navy);c.rect(margin,top,W-2*margin,header_h,fill=1,stroke=0)
            text(margin+10,top+10,'TIME',10,colors.white,True)
            dw=(W-2*margin-time_w)/len(panel)
            height=top-bottom
            def yy(minute):return top-(minute-start)/(end-start)*height
            for i,day in enumerate(panel):
                x=margin+time_w+i*dw
                c.setFillColor(colors.white);c.setFont(bold,10.3)
                c.drawCentredString(x+dw/2,top+10,day['date'].strftime('%A %d %b')+' | '+day['mode'].title())
                c.setFillColor(colors.HexColor('#F7F9FB'));c.rect(x,bottom,dw,height,fill=1,stroke=0)
                # Preserve each configured interval, including possible gaps.
                bounds=[start,*[v for w in day['windows'] for v in (w.start_minute,w.end_minute)],end]
                for a,b in zip(bounds,bounds[1:]):
                    if a<b and not any(w.start_minute<=a and b<=w.end_minute for w in day['windows']):
                        c.setFillColor(colors.HexColor('#E5EAF0'));c.rect(x,yy(b),dw,yy(a)-yy(b),fill=1,stroke=0)
                c.setFillColor(colors.HexColor('#FFF0CA'));c.rect(x,yy(lunch.end_minute),dw,yy(lunch.start_minute)-yy(lunch.end_minute),fill=1,stroke=0)
                c.setFillColor(colors.HexColor('#856322'));c.setFont(bold,9)
                c.drawCentredString(x+dw/2,(yy(lunch.start_minute)+yy(lunch.end_minute))/2-3,
                    f'{clock(lunch.start_minute)}-{clock(lunch.end_minute)} | STUDENT LUNCH')
            c.setStrokeColor(grid);c.setLineWidth(.4)
            for minute in range(start,end+1,30):
                y=yy(minute);c.line(margin,y,margin+time_w,y)
                for i,day in enumerate(panel):
                    if not lunch.start_minute<minute<lunch.end_minute and any(w.start_minute<=minute<=w.end_minute for w in day['windows']):
                        x=margin+time_w+i*dw;c.line(x,y,x+dw,y)
                if minute<end:text(margin+3,(y+yy(minute+30))/2-3,f'{clock(minute)}-{clock(minute+30)}',8.3,muted)
            for i in range(len(panel)+1):
                x=margin+time_w+i*dw;c.line(x,bottom,x,top)
            c.rect(margin,bottom,W-2*margin,height,fill=0,stroke=1)
            day_index={d['day']:i for i,d in enumerate(panel)}
            for booking,lane,count in lanes:
                if booking.day not in day_index:continue
                width=dw/count
                x=margin+time_w+day_index[booking.day]*dw+lane*width+3
                ytop,ybottom=yy(booking.start_minute)-2,yy(booking.end_minute)+2
                p,kind=paragraph_for(booking,view)
                _,text_h=p.wrap(width-18,H)
                assert text_h <= ytop-ybottom-6+.01
                c.setFillColor(colors.HexColor(fills[kind]));c.setStrokeColor(colors.HexColor(accents[kind]));c.setLineWidth(.5)
                c.roundRect(x,ybottom,width-6,ytop-ybottom,3,fill=1,stroke=1)
                p.drawOn(c,x+6,ytop-3-text_h)
                manifest.append(dict(page=page,main_group_id=view.main_group_id,delivery_id=booking_identity(booking),
                    attendance_group_ids=list(booking_groups(booking)),attendance_labels=[view.labels[g] for g in booking_groups(booking)],
                    lane=lane,lane_count=count,font_size=9.2,text_height=text_h,available_height=ytop-ybottom-6))
            text(margin,52,'Blue: Lecture   Green: Tutorial   Purple: Lab   Orange: Workshop | Grey: outside teaching window | Empty: no saved booking',8.5,muted)
            text(margin,35,'Source hours and attendance preserved. Local corrected preview; estimated PP1 counts; no official enrollment-start claim.',8.5,navy)
            text(margin,22,f'{batch.configuration.output_label} | Reference week: {calendar.week_start} to {calendar.week_end} | Main Group {view.labels[main.group_id]} | Part {part}/{len(panels)}',8,muted)
            c.setFont(regular,8.5);c.drawRightString(W-margin,22,f'{page}/{page_count}')
            c.showPage()
    c.save()
    expected={booking_identity(b) for b in selected}
    if {r['delivery_id'] for r in manifest} != expected:
        raise ValueError('PDF coverage does not equal selected validated deliveries.')
    return PdfExportReport(page_count,len(views),len(expected),len(manifest),page_count-len(views),tuple(manifest))
