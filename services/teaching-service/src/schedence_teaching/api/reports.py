"""Academic Admin workload reads and downloads; no new authentication or writes."""
import asyncio
from typing import Annotated

from fastapi import APIRouter,Depends,Query,Response

from .authorization import authorize
from .errors import ApiError
from .routes import authenticate
from schedence_teaching.reporting.contracts import WorkloadQuery,WorkloadExportQuery
from schedence_teaching.reporting.service import WorkloadReadService
from schedence_teaching.reporting.exports import workload_csv,workload_pdf

router=APIRouter(prefix='/reports/lecturer-workload',tags=['lecturer workload reports'])
Auth=Annotated[tuple,Depends(authenticate)]


async def read_report(auth,query):
    runtime,principal=auth
    access=await authorize(principal,query.institution_id,runtime.bindings)
    return await WorkloadReadService(runtime.store).report(access,query)


@router.get('')
async def summary(auth:Auth,query:Annotated[WorkloadQuery,Query()]):
    """One selected validated timetable version; includes zero-assignment staff."""
    return await read_report(auth,query)


@router.get('/export',response_class=Response,responses={200:{'description':'Private workload report download',
    'content':{'application/pdf':{'schema':{'type':'string','format':'binary'}},
               'text/csv':{'schema':{'type':'string','format':'binary'}}}}})
async def export(auth:Auth,query:Annotated[WorkloadExportQuery,Query()]):
    """Private PDF or CSV. No automatic publication or production mutation."""
    base=WorkloadQuery.model_validate({k:v for k,v in query.model_dump().items() if k in WorkloadQuery.model_fields})
    report=await read_report(auth,base)
    if query.export_format=='pdf':
        if query.kind not in {'summary','individual','detailed'}:raise ApiError(422,'INVALID_EXPORT','PDF supports summary, individual or detailed.')
        try:body=await asyncio.to_thread(workload_pdf,report,query.kind)
        except ImportError:raise ApiError(503,'PDF_UNAVAILABLE','Install the Teaching Service PDF extra.') from None
        suffix={'summary':'Summary','individual':'Individual','detailed':'Detailed'}[query.kind]
        name='Individual_Lecturer_Workload.pdf' if query.kind=='individual' else f'Lecturer_Workload_{suffix}.pdf'
        media='application/pdf'
    else:
        if query.kind not in {'summary','assignments','targets','fairness'}:raise ApiError(422,'INVALID_EXPORT','CSV supports summary, assignments, targets or fairness.')
        body=await asyncio.to_thread(workload_csv,report,query.kind)
        name=f'Lecturer_Workload_{query.kind.title()}.csv';media='text/csv'
    return Response(body,media_type=media,headers={'Content-Disposition':f'attachment; filename="{name}"','Cache-Control':'no-store'})


@router.get('/{staff_id}')
async def individual(staff_id:str,auth:Auth,query:Annotated[WorkloadQuery,Query()]):
    """Individual staff report, with the same institution-wide read authorization."""
    if query.lecturer not in (None,staff_id):raise ApiError(422,'STAFF_FILTER_CONFLICT','Path staff and lecturer filter must match.')
    return await read_report(auth,query.model_copy(update=dict(lecturer=staff_id)))
