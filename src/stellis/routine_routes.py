"""Explicit source-bound routine review endpoints."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from stellis import routine_review, agent_gateway
router=APIRouter()

def checked(fn):
    try:return fn()
    except LookupError as exc:raise HTTPException(404,str(exc)) from exc
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc

@router.get('/care-review/{kind}/{identifier}')
def get(kind:str,identifier:str):return checked(lambda:routine_review.get(kind,identifier))

@router.post('/care-review/{kind}/{identifier}/prepare')
async def prepare(kind:str,identifier:str):
    try:return await routine_review.prepare(kind,identifier)
    except LookupError as exc:raise HTTPException(404,str(exc)) from exc
    # A review whose answers fail the source checks is an expected outcome, shown beside Retry, not a failed request.
    except ValueError as exc:return {'state':'failed','detail':str(exc),'proposals':[]}
    except agent_gateway.GatewayError as exc:raise HTTPException(503,str(exc)) from exc

class Selection(BaseModel):
    selected:list[str]=Field(min_length=1,max_length=20)

@router.post('/care-review/{identifier}/apply')
def apply(identifier:str,request:Selection):return checked(lambda:routine_review.apply(identifier,request.selected))

@router.post('/care-review/{identifier}/dismiss')
def dismiss(identifier:str):return checked(lambda:routine_review.dismiss(identifier))

@router.post('/care-review/{identifier}/undo')
def undo(identifier:str):return checked(lambda:routine_review.undo(identifier))

class Edit(BaseModel):
    data:dict

@router.put('/care-review/{identifier}/items/{proposal_id}')
def edit(identifier:str,proposal_id:str,request:Edit):return checked(lambda:routine_review.edit(identifier,proposal_id,request.data))

class FollowupApproval(BaseModel):
    visit_id:str=Field(max_length=64)
    question:str|None=Field(default=None,max_length=240)
    time_zone:str=Field(default='UTC',max_length=80)

@router.post('/report-followups/{identifier}/approve')
def approve_followup(identifier:str,request:FollowupApproval):
    from stellis import report_followups
    return checked(lambda:report_followups.approve(identifier,request.visit_id,request.question,request.time_zone))

@router.post('/report-followups/{identifier}/dismiss')
def dismiss_followup(identifier:str):
    from stellis import report_followups
    return checked(lambda:report_followups.dismiss(identifier))

@router.get('/care-events/{visit_id}/preparation')
def visit_preparation(visit_id:str):
    from stellis import report_followups
    return checked(lambda:report_followups.preparation(visit_id))

@router.get('/coach/calls')
def calls():
    from stellis import call_history
    return checked(call_history.list_all)

@router.get('/coach/calls/{identifier}')
def call_history_detail(identifier:str):
    from stellis import call_history
    return checked(lambda:call_history.get(identifier))

class CallReceipt(BaseModel):
    batch_id:str=Field(max_length=64)

@router.post('/coach/calls/{identifier}/receipts')
def call_receipt(identifier:str,request:CallReceipt):
    from stellis import call_history
    return checked(lambda:call_history.receipt(identifier,request.batch_id))

@router.post('/report-exchanges/{identifier}/visit-question')
async def prepare_visit_question(identifier:str):
    from stellis import report_followups
    try:return await report_followups.prepare(identifier)
    except LookupError as exc:raise HTTPException(404,str(exc)) from exc
    except (ValueError,agent_gateway.GatewayError) as exc:raise HTTPException(422,str(exc)) from exc
