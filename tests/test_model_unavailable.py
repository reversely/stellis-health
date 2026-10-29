"""A model service outage answers 503 with a readable message, never a plain 500 (#226 re-record)."""
from fastapi.testclient import TestClient

from stellis import agent_gateway, report_context, server


def test_gateway_error_is_a_readable_503(tmp_path, monkeypatch):
    monkeypatch.setenv('STELLIS_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('STELLIS_AUTH_MODE', 'off')

    async def unavailable(*_args, **_kwargs):
        raise agent_gateway.GatewayError('Token Factory is unavailable. Try again.')

    monkeypatch.setattr(report_context, 'ask', unavailable)
    client = TestClient(server.app, raise_server_exceptions=False)
    response = client.post('/api/v1/documents/doc/explanations',
                           json={'map_id': 'm', 'region_id': 'r', 'question': 'What does this mean?'},
                           headers={'Origin': 'http://testserver'})
    assert response.status_code == 503
    assert response.json() == {'detail': 'Token Factory is unavailable. Try again.'}
