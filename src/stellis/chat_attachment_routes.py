"""Account-scoped upload, history and notebook filing for chat attachments."""

import asyncio
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from stellis import chat_attachments, document_ocr, documents, health_facts

router = APIRouter()
ThreadKind = Literal['agent', 'notebook']


def checked(action):
    try:
        return action()
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.get('/chat/{kind}/{thread_id}/attachments')
def attachments(kind: ThreadKind, thread_id: str):
    return checked(lambda: chat_attachments.list_all(kind, thread_id))


@router.post('/chat/{kind}/{thread_id}/attachments')
async def attach(kind: ThreadKind, thread_id: str, file: UploadFile = File(...)):
    content = await file.read(documents.MAX_BYTES + 1)
    item = await asyncio.to_thread(checked, lambda: chat_attachments.add(kind, thread_id, file.filename or '', content))
    record = documents.get(item['document_id'])
    if record['status'] == 'needs_text':
        try:
            await asyncio.to_thread(document_ocr.read_page, record['id'], 1)
        except (document_ocr.OcrError, documents.DocumentConflict, ValueError):
            # Retain unreadable files for manual text correction and notebook filing.
            pass
    await asyncio.to_thread(health_facts.prepare, record['id'])
    return checked(lambda: next(row for row in chat_attachments.list_all(kind, thread_id) if row['id'] == item['id']))


class Filing(BaseModel):
    notebook_id: str = Field(min_length=1, max_length=100)


@router.post('/chat/{kind}/{thread_id}/attachments/{attachment_id}/notebook')
def save_attachment(kind: ThreadKind, thread_id: str, attachment_id: str, request: Filing):
    return checked(lambda: chat_attachments.save(kind, thread_id, attachment_id, request.notebook_id))


@router.delete('/chat/{kind}/{thread_id}/attachments/{attachment_id}')
def remove_attachment(kind: ThreadKind, thread_id: str, attachment_id: str):
    checked(lambda: chat_attachments.remove(kind, thread_id, attachment_id))
    return {'removed': True}
