"""Authenticated API for source-anchored report explanations."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from stellis import document_ocr, documents, notebook_chat, report_context

router = APIRouter()


def _error(exc):
    code = (409 if isinstance(exc, documents.DocumentConflict) else
            404 if isinstance(exc, LookupError) else
            503 if isinstance(exc, (document_ocr.OcrError, notebook_chat.ChatError)) else 400)
    return HTTPException(status_code=code, detail=str(exc))


@router.get('/documents/{document_id}/pages/{number}/original')
def original(document_id: str, number: int):
    try:
        return Response(report_context.page_image(document_id, number), media_type='image/png',
                        headers={'Cache-Control': 'private, no-store'})
    except (LookupError, ValueError, document_ocr.OcrError) as exc:
        raise _error(exc) from exc


@router.get('/documents/{document_id}/pages/{number}/map')
def mapped(document_id: str, number: int):
    try:
        return report_context.page_map(document_id, number)
    except (LookupError, ValueError, document_ocr.OcrError) as exc:
        raise _error(exc) from exc


@router.get('/documents/{document_id}/explanations')
def explanations(document_id: str):
    try:
        return {'exchanges': report_context.history(document_id)}
    except LookupError as exc:
        raise _error(exc) from exc


class Question(BaseModel):
    map_id: str = Field(max_length=80)
    region_id: str = Field(max_length=400)
    question: str = Field(min_length=1, max_length=2000)


@router.post('/documents/{document_id}/explanations')
async def ask(document_id: str, request: Question):
    try:
        return await report_context.ask(document_id, request.map_id, request.region_id, request.question)
    except (LookupError, ValueError, document_ocr.OcrError, notebook_chat.ChatError) as exc:
        raise _error(exc) from exc

class Handoff(BaseModel):
    map_id:str=Field(max_length=80)
    region_id:str=Field(max_length=400)
    purpose:str=Field(max_length=10)

@router.post('/documents/{document_id}/handoff')
def handoff(document_id:str,request:Handoff):
    from stellis import source_handoffs
    try:return source_handoffs.create(document_id,request.map_id,request.region_id,request.purpose)
    except (LookupError,ValueError,documents.DocumentConflict) as exc:raise _error(exc) from exc

@router.get('/source-handoffs/{identifier}')
def get_handoff(identifier:str):
    from stellis import source_handoffs
    try:return source_handoffs.resolve(identifier)
    except (LookupError,ValueError,documents.DocumentConflict) as exc:raise _error(exc) from exc
