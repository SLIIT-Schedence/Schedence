"""PDFs project validated bookings into one timetable per main group."""
from dataclasses import replace

import pytest

from schedence_teaching.pilot.pdf_export import export_main_group_pdf
from schedence_teaching.solver import SolverConfig, solve_timetable

from test_pp1_allocations import configuration, small_batch


@pytest.fixture(scope='module')
def pdf_schedule():
    pytest.importorskip('reportlab')
    config=configuration()
    raw=config.solver_config.model_dump()
    raw['delivery_calendar']=dict(reference='synthetic:explicit-pdf-calendar',days=[
        dict(day=d.day,stream=s,mode='PHYSICAL',allowed_session_types=['LECTURE','LAB','TUTORIAL','WORKSHOP'])
        for d in config.solver_config.days for s in d.windows])
    config.solver_config=SolverConfig.model_validate(raw)
    batch=small_batch(lab=2,config=config)
    result=solve_timetable(batch.prepared,batch.config,**batch.solver_inputs)
    return batch,result


def test_pdf_default_is_one_main_group_page_with_all_subgroups(pdf_schedule,tmp_path):
    pypdf=pytest.importorskip('pypdf')
    batch,result=pdf_schedule
    path=tmp_path/'unified.pdf'
    report=export_main_group_pdf(batch,result,path,academic_year=1,semester=1,streams=['WEEKDAY'])
    assert report.main_group_views==2 and report.unique_deliveries==6
    assert report.displayed_deliveries==6
    assert report.pages>=report.main_group_views
    assert report.continuation_pages==report.pages-report.main_group_views
    reader=pypdf.PdfReader(path)
    assert len(reader.pages)==report.pages
    all_text=' '.join(p.extract_text() for p in reader.pages)
    assert 'Subgroups:' in all_text and 'G1.1' in all_text and 'G1.2' in all_text
    assert 'Lab subgroup' not in all_text
    assert 'Synthetic Name' in all_text
    assert all(r['font_size']==9.2 and r['text_height']<=r['available_height'] for r in report.manifest)


def test_partial_results_do_not_create_pdf(pdf_schedule,tmp_path):
    batch,result=pdf_schedule
    path=tmp_path/'partial.pdf'
    with pytest.raises(ValueError):
        export_main_group_pdf(batch,replace(result,schedule=result.schedule[:-1]),path,
            academic_year=1,semester=1,streams=['WEEKDAY'])
    assert not path.exists()


def test_export_does_not_overwrite_existing_pdf(pdf_schedule,tmp_path):
    batch,result=pdf_schedule
    path=tmp_path/'existing.pdf'
    path.write_bytes(b'original')
    with pytest.raises(FileExistsError):
        export_main_group_pdf(batch,result,path,academic_year=1,semester=1,streams=['WEEKDAY'])
    assert path.read_bytes()==b'original'


def test_empty_scope_does_not_create_a_pdf(pdf_schedule,tmp_path):
    batch,result=pdf_schedule
    path=tmp_path/'missing.pdf'
    with pytest.raises(ValueError,match='no student groups'):
        export_main_group_pdf(batch,result,path,academic_year=4,semester=2,streams=['WEEKEND'])
    assert not path.exists()
