"""Validated tracker endpoints on the authenticated API router."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StrictBool
from stellis import reproductive, health_preferences, calendar, health_observations

router = APIRouter()


class TrackerIn(BaseModel):
    start: str = Field(max_length=10)
    end: str = Field(default='', max_length=10)
    name: str = Field(default='', max_length=120)
    note: str = Field(default='', max_length=500)
    flow: str = Field(default='Moderate', pattern='^(Spotting|Light|Moderate|Heavy)$')
    active: int = Field(default=21, ge=1, le=365)
    break_days: int = Field(default=7, ge=0, le=365)
    time: str = Field(default='09:00', pattern=r'^\d{2}:\d{2}$')
    type: str = Field(default='Hormonal IUD', max_length=30)
    renewal: str = Field(default='', max_length=10)
    notice: int = Field(default=30, ge=0, le=365)
    reminder: bool = False
    tracking_gap_before: bool = False


def checked(action):
    try:
        return action()
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get('/reproductive')
def records():
    return reproductive.list_records()


@router.post('/reproductive/{kind}')
def save(kind: str, request: TrackerIn, record_id: str | None = None):
    return checked(lambda: reproductive.save(kind, request.model_dump(), record_id))


@router.delete('/reproductive/{record_id}')
def delete(record_id: str):
    return reproductive.delete(record_id)


class DoseIn(BaseModel):
    day: str = Field(max_length=10)
    taken: bool = False
    status: Literal["taken", "skipped"] | None = None


@router.put('/reproductive/{record_id}/dose')
def dose(record_id: str, request: DoseIn):
    return checked(lambda: reproductive.dose(record_id, request.day, request.taken, request.status))


class HealthPreferencesIn(BaseModel):
    experiences_periods: StrictBool | None = None


@router.get('/health/preferences')
def health_feature_preferences():
    return health_preferences.preferences()


@router.put('/health/preferences')
def save_health_feature_preferences(request: HealthPreferencesIn):
    return health_preferences.save(request.experiences_periods)


@router.get('/calendar/events')
async def calendar_event_view(start: str, end: str, utc_offset: int = Query(default=0, ge=-840, le=840), time_zone: str | None = Query(default=None, max_length=100)):
    try:
        return await calendar.event_view(start, end, utc_offset, time_zone)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


class ObservationIn(BaseModel):
    day: str = Field(max_length=10)
    track_id: Literal['periods']
    value: Literal['Had flow', 'No bleeding', 'Spotting', 'Light', 'Moderate', 'Heavy'] | None = None


@router.get('/health/observations')
def daily_observations():
    return {'observations': health_observations.entries()}


@router.put('/health/observations')
def save_daily_observation(request: ObservationIn):
    return checked(lambda: health_observations.save(request.day, request.track_id, request.value))
