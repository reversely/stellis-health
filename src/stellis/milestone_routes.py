"""Workspace milestone endpoints shared by countdowns and calendar events."""
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from stellis import health_milestones

router = APIRouter()


class MilestoneIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    target_date: str = Field(min_length=10, max_length=10)
    date_kind: Literal['scheduled', 'estimated'] = 'scheduled'
    actual_date: str = Field(default='', max_length=10)
    note: str = Field(default='', max_length=500)


@router.get('/calendar/milestones')
def milestones():
    return {'milestones': health_milestones.entries()}


@router.post('/calendar/milestones')
@router.put('/calendar/milestones/{milestone_id}')
def save_milestone(request: MilestoneIn, milestone_id: str | None = None):
    try:
        return health_milestones.save(request.model_dump(), milestone_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.delete('/calendar/milestones/{milestone_id}')
def delete_milestone(milestone_id: str):
    return health_milestones.delete(milestone_id)
