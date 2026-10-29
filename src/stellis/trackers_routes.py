"""Tracker registry endpoints on the authenticated API router."""
import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from stellis import tracker_log, trackers

router = APIRouter()
# A form has at most a few dozen fields; reject oversized bodies early.
MAX_FIELDS = 40


def checked(action):
    try:
        return action()
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def _today(value):
    if value is None:
        return None
    return checked(lambda: dt.date.fromisoformat(value))


def _fields(data):
    if len(data) > MAX_FIELDS:
        raise HTTPException(422, 'Too many fields')
    return data


@router.get('/trackers/kinds')
def kinds():
    return {'kinds': trackers.kinds()}


@router.get('/trackers')
def records(today: str | None = Query(default=None, max_length=10),
            notebook_id: str | None = Query(default=None, max_length=64)):
    return {'records': trackers.list_records(notebook_id=notebook_id, today=_today(today))}


# Fixed paths come before /trackers/{kind}, which would otherwise match them.
@router.get('/trackers/log/items')
def log_items(today: str | None = Query(default=None, max_length=10)):
    return {'items': tracker_log.items(_today(today)), 'max': tracker_log.MAX_ENTRIES}


class EntryIn(BaseModel):
    item: str = Field(max_length=120)
    value: str | float | bool | None = None
    value2: float | None = None
    amount: float | None = Field(default=None, gt=0)
    time:str|None=Field(default=None,pattern=r'^\d{2}:\d{2}$')
    evidence: str = Field(default='', max_length=500)
    taken_at:str|None=Field(default=None,max_length=64)
    time_zone:str|None=Field(default=None,max_length=80)


class EntriesIn(BaseModel):
    day: str = Field(max_length=10)
    time: str | None = Field(default=None, pattern=r'^\d{2}:\d{2}$')
    time_zone:str|None=Field(default=None,max_length=80)
    entries: list[EntryIn] = Field(min_length=1, max_length=tracker_log.MAX_ENTRIES)


@router.post('/trackers/entries')
def save_entries(request: EntriesIn):
    try:
        return tracker_log.save([entry.model_dump() for entry in request.entries], request.day, request.time, request.time_zone)
    except tracker_log.EntryErrors as error:
        raise HTTPException(422, {'message': 'Nothing was saved. Check the marked items.',
                                  'errors': {str(k): v for k, v in error.errors.items()}}) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.delete('/trackers/entries/{batch_id}')
def undo_entries(batch_id: str):
    try:
        return tracker_log.undo(batch_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.post('/trackers/{kind}')
def create(kind: str, data: dict[str, Any] = Body(...)):
    return checked(lambda: trackers.save(kind, _fields(data)))


@router.put('/trackers/records/{record_id}')
def update(record_id: str, data: dict[str, Any] = Body(...)):
    stored = next((r for r in trackers.list_records() if r['id'] == record_id), None)
    if stored is None:
        raise HTTPException(404, 'Tracked item was not found')
    return checked(lambda: trackers.save(stored['kind'], _fields(data), record_id))


@router.delete('/trackers/records/{record_id}')
def delete(record_id: str):
    return trackers.delete(record_id)


class OutcomeIn(BaseModel):
    status: Literal['taken', 'skipped'] | None = None
    value: float | None = Field(default=None, gt=0)
    taken_at:str|None=Field(default=None,max_length=64)
    time_zone:str|None=Field(default=None,max_length=80)


@router.put('/trackers/records/{record_id}/outcomes/{day}')
def outcome(record_id: str, day: str, request: OutcomeIn):
    return checked(lambda: trackers.log(record_id, day, request.status, request.value,taken_at=request.taken_at,time_zone=request.time_zone))
