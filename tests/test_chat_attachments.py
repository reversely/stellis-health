"""Attachments retain chat evidence and file canonical documents in notebooks."""

import asyncio
from unittest.mock import AsyncMock, patch

import pymupdf
import pytest
from fastapi.testclient import TestClient

from stellis import agents, chat_attachments, documents, notebook_chat, notebook_sources, notebooks, storage
from stellis.server import app


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('STELLIS_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('STELLIS_AUTH_MODE', 'off')
    book = notebooks.create('Attachment review')
    return book, agents.assistant()['id'], TestClient(app)


def pdf(text='Glucose 89 mg/dL'):
    with pymupdf.open() as record:
        record.new_page().insert_text((50, 80), text)
        return record.tobytes()


def test_upload_file_and_reload_without_duplicate_entries(workspace):
    book, agent, client = workspace
    path = f'/api/v1/chat/agent/{agent}/attachments'
    response = client.post(path, files={'file': ('result.pdf', pdf(), 'application/pdf')})
    assert response.status_code == 200, response.text
    attachment = response.json()
    assert attachment['notebook_id'] is None
    assert attachment['status'] == 'ready'
    for _ in range(2):
        saved = client.post(path + '/' + attachment['id'] + '/notebook', json={'notebook_id': book['id']})
        assert saved.status_code == 200, saved.text
    sources = notebook_sources.list_sources(book['id'])
    assert [item['id'] for item in sources] == ['document:' + attachment['document_id']]
    assert client.get(path).json()[0]['notebook_id'] == book['id']
    assert documents.get(attachment['document_id'])['filename'] == 'result.pdf'


def test_notebook_chat_reads_attachment_and_retains_message_link(workspace):
    book, _, client = workspace
    item = chat_attachments.add('notebook', book['id'], 'result.pdf', pdf())
    gateway = AsyncMock(return_value=('Glucose is 89 mg/dL. [[source:1]]', 'session'))
    with patch('stellis.agent_gateway.turn', gateway):
        result = asyncio.run(notebook_chat.ask(book['id'], 'What is the glucose?', [], attachment_ids=[item['id']]))
    assert result['citations'][0]['target'].startswith('/document/' + item['document_id'])
    assert '89 mg/dL' in gateway.await_args.args[0]
    message = notebook_chat.messages(book['id'])[0]
    assert chat_attachments.list_all('notebook', book['id'])[0]['message_id'] == message['id']
    assert documents.get(item['document_id'])['notebook_id'] is None
    with pytest.raises(ValueError, match='unavailable'):
        chat_attachments.selected('notebook', book['id'], [item['id']])


def test_cross_thread_and_cross_workspace_access_is_rejected(workspace, tmp_path, monkeypatch):
    book, agent, client = workspace
    item = chat_attachments.add('agent', agent, 'private.pdf', pdf('Private evidence'))
    with pytest.raises(ValueError, match='unavailable'):
        chat_attachments.selected('notebook', book['id'], [item['id']])
    path = f'/api/v1/chat/notebook/{book["id"]}/attachments/{item["id"]}/notebook'
    assert client.post(path, json={'notebook_id': book['id']}).status_code == 404
    monkeypatch.setenv('STELLIS_DATA_DIR', str(tmp_path / 'other-account'))
    with pytest.raises(LookupError):
        chat_attachments.list_all('agent', agent)


def test_failed_reply_keeps_pending_files_and_removal_keeps_original(workspace):
    book, _, _ = workspace
    item = chat_attachments.add('notebook', book['id'], 'result.pdf', pdf())
    with patch('stellis.agent_gateway.turn', AsyncMock(side_effect=notebook_chat.agent_gateway.GatewayError('Unavailable'))):
        with pytest.raises(notebook_chat.ChatError):
            asyncio.run(notebook_chat.ask(book['id'], 'Read this', [], attachment_ids=[item['id']]))
    assert chat_attachments.selected('notebook', book['id'], [item['id']])
    chat_attachments.remove('notebook', book['id'], item['id'])
    assert documents.get(item['document_id'])['id'] == item['document_id']
    assert chat_attachments.list_all('notebook', book['id']) == []


def test_file_validation_limit_and_existing_notebook_are_preserved(workspace):
    book, agent, client = workspace
    path = f'/api/v1/chat/agent/{agent}/attachments'
    assert client.post(path, files={'file': ('bad.pdf', b'not a pdf')}).status_code == 400
    content=pdf()
    record, _ = documents.create('result.pdf', content, book['id'])
    item = chat_attachments.add('agent', agent, 'result.pdf', content)
    other = notebooks.create('Other notebook')
    with pytest.raises(ValueError, match='another notebook'):
        chat_attachments.save('agent', agent, item['id'], other['id'])
    assert documents.get(record['id'])['notebook_id'] == book['id']
    for index in range(4):
        chat_attachments.add('agent', agent, f'{index}.pdf', pdf(str(index)))
    with pytest.raises(ValueError, match='current attachments'):
        chat_attachments.add('agent', agent, 'extra.pdf', pdf('extra'))


def test_agent_reply_receives_evidence_and_binds_attachment(workspace):
    _, agent, client = workspace
    item = chat_attachments.add('agent', agent, 'result.pdf', pdf())
    message = agents.append_message(agent, 'user', 'Read this')
    result = {'user_message':message,'agent_message':{'content':'89 mg/dL'}}
    with patch('stellis.agent_runtime.ask', AsyncMock(return_value=result)) as runtime:
        response = client.post(f'/api/v1/agents/{agent}/ask', json={'question':'Read this','attachment_ids':[item['id']]})
    assert response.status_code == 200, response.text
    assert '89 mg/dL' in runtime.await_args.args[3][0]['excerpt']
    assert chat_attachments.list_all('agent', agent)[0]['message_id'] == message['id']
    replay = client.post(f'/api/v1/agents/{agent}/ask', json={'question':'Read this','attachment_ids':[item['id']]})
    assert replay.status_code == 400


def test_missing_ocr_retains_file_with_explicit_needs_text_status(workspace):
    import io
    from PIL import Image
    _, agent, client = workspace
    content = io.BytesIO()
    Image.new('RGB', (20,20), 'white').save(content, format='PNG')
    with patch('stellis.document_ocr.shutil.which', return_value=None):
        result = client.post(f'/api/v1/chat/agent/{agent}/attachments', files={'file':('scan.png',content.getvalue(),'image/png')})
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'needs_text'
    assert documents.file_path(result.json()['document_id'])[0].exists()
