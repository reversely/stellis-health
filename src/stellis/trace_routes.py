"""Per-answer request traces on the authenticated API router."""
from fastapi import APIRouter, HTTPException

from stellis import answer_trace

router = APIRouter()


def _found(action):
    try:
        return action()
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.get('/traces/job/{job_id}')
def by_job(job_id: str):
    return _found(lambda: answer_trace.for_job(job_id))


@router.get('/traces/{trace_id}')
def by_id(trace_id: str):
    return _found(lambda: answer_trace.for_trace(trace_id))
