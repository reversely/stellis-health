"""Execution/inference separation and credential-scoped speech contracts (#207)."""
import asyncio,json,os
from unittest.mock import patch
import httpx,pytest
from stellis import agent_gateway,agent_runtime,ai,call_tts,runtime_profile,runtime_settings,storage

@pytest.fixture(autouse=True)
def account(tmp_path,monkeypatch):
 with patch.dict(os.environ,{'STELLIS_DATA_DIR':str(tmp_path),'STELLIS_ACCOUNT_ID':'1'},clear=True):yield

@pytest.mark.parametrize('profile,provider,url',[('cloud','Token Factory','https://api.tokenfactory.nebius.com/v1'),('local','Ollama','http://127.0.0.1:11434/v1')])
def test_direct_tools_do_not_require_or_claim_a_managed_sandbox(profile,provider,url):
 os.environ.update(STELLIS_RUNTIME_PROFILE=profile,STELLIS_AGENT_BACKEND='direct',NEBIUS_API_KEY='cloud-test-key')
 assert runtime_profile.direct() and runtime_profile.remote_agents()
 assert runtime_profile.inference_url()==url and runtime_profile.inference_provider()==provider
 assert 'NemoClaw' not in runtime_profile.agent_execution()
 assert agent_runtime.configured();assert ai._managed_sandbox() is None
 assert runtime_profile.deployment()=='unspecified'
 if profile=='local':assert ai._key()=='ollama'


def test_openai_local_server_uses_its_own_credential_and_no_ollama_management():
 os.environ.update(STELLIS_RUNTIME_PROFILE='local',STELLIS_AGENT_BACKEND='direct',STELLIS_LOCAL_INFERENCE_PROVIDER='openai',STELLIS_LOCAL_INFERENCE_URL='http://127.0.0.1:8000/v1',STELLIS_LOCAL_INFERENCE_TOKEN='local-secret',NEBIUS_API_KEY='cloud-secret')
 assert ai._key()=='local-secret';assert 'OpenAI-compatible' in runtime_profile.inference_provider()
 def handle(req):
  assert str(req.url)=='http://127.0.0.1:8000/v1/chat/completions'
  assert req.headers['authorization']=='Bearer local-secret';assert b'cloud-secret' not in req.content
  body=json.loads(req.content);assert body['tools'][0]['function']['name']=='check_connection'
  return httpx.Response(200,json={'choices':[{'message':{'tool_calls':[{'function':{'name':'check_connection'}}]}}]})
 with patch.object(runtime_settings.httpx,'Client',return_value=httpx.Client(transport=httpx.MockTransport(handle))):
  assert runtime_settings._capabilities('local-model')==['completion','tools']


def test_nebius_voice_is_explicit_elena_and_status_never_contains_secrets():
 os.environ.update(STELLIS_TTS_BACKEND='nebius',STELLIS_CHATTERBOX_NEBIUS_URL='https://tts.example',STELLIS_CHATTERBOX_NEBIUS_TOKEN='private-token',STELLIS_CHATTERBOX_VOICE='Elena.wav')
 def handle(req):
  assert str(req.url)=='https://tts.example/v1/audio/voices';assert req.headers['authorization']=='Bearer private-token'
  return httpx.Response(200,json={'voices':['Elena.wav','Other.wav']})
 with patch.object(call_tts.httpx,'Client',return_value=httpx.Client(transport=httpx.MockTransport(handle))):state=call_tts.check()
 assert state['state']=='voice_available' and state['voice']=='Elena.wav'
 assert 'private-token' not in json.dumps(state) and 'tts.example' not in json.dumps(state)


def test_missing_elena_never_silently_selects_another_voice():
 os.environ.update(STELLIS_TTS_BACKEND='nebius',STELLIS_CHATTERBOX_NEBIUS_URL='https://tts.example',STELLIS_CHATTERBOX_NEBIUS_TOKEN='private-token',STELLIS_CHATTERBOX_VOICE='Elena.wav')
 with patch.object(call_tts.httpx,'Client',return_value=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'voices':['Other.wav']})))):
  assert call_tts.check()['state']=='voice_missing'
 assert call_tts.status()['voice']=='Elena.wav'


def test_execution_choice_is_validated_separately_from_profile():
 os.environ.update(STELLIS_RUNTIME_PROFILE='local',STELLIS_AGENT_BACKEND='typo')
 with pytest.raises(ValueError,match='STELLIS_AGENT_BACKEND'):runtime_profile.direct()


def test_managed_gateway_receives_default_role_model_without_claiming_local_sandbox():
 os.environ.update(STELLIS_RUNTIME_PROFILE='cloud',STELLIS_AGENT_BACKEND='gateway',STELLIS_ACCOUNT_ID='1',STELLIS_AGENT_GATEWAY_ACCOUNT_ID='1',STELLIS_AGENT_GATEWAY_URL='https://agents.example',STELLIS_AGENT_GATEWAY_TOKEN='gateway-secret')
 def handle(req):
  assert req.headers['x-openclaw-model']=='nebius/nvidia/nemotron-3-super-120b-a12b'
  assert req.headers['authorization']=='Bearer gateway-secret';assert b'gateway-secret' not in req.content
  return httpx.Response(200,json={'status':'completed','output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'A response'}]}]})
 client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
 with patch.object(agent_gateway.httpx,'AsyncClient',return_value=client):
  answer,_=asyncio.run(agent_gateway.turn('Check source evidence','test-review',instructions='Return an answer.',model_role='review'))
 assert answer=='A response';assert runtime_profile.agent_execution()=='Managed HTTP agent gateway'


def test_cookbook_speech_without_voice_listing_does_not_claim_voice_availability():
 os.environ.update(STELLIS_TTS_BACKEND='nebius',STELLIS_CHATTERBOX_NEBIUS_URL='https://tts.example',STELLIS_CHATTERBOX_NEBIUS_TOKEN='private-token',STELLIS_CHATTERBOX_VOICE='Elena.wav')
 def handle(req):
  assert req.headers['authorization']=='Bearer private-token'
  if req.url.path=='/v1/audio/voices':return httpx.Response(404,json={'detail':'Not Found'})
  assert req.url.path=='/v1/models'
  return httpx.Response(200,json={'data':[{'id':'tts-1'}]})
 with patch.object(call_tts.httpx,'Client',return_value=httpx.Client(transport=httpx.MockTransport(handle))):state=call_tts.check()
 assert state['state']=='voice_unverified' and state['voice']=='Elena.wav'
 assert state['voice_listing_supported'] is False
 assert 'private-token' not in json.dumps(state)


def test_missing_tunnel_is_still_unavailable_when_both_routes_return_404():
 os.environ.update(STELLIS_TTS_BACKEND='nebius',STELLIS_CHATTERBOX_NEBIUS_URL='https://tts.example',STELLIS_CHATTERBOX_NEBIUS_TOKEN='private-token')
 with patch.object(call_tts.httpx,'Client',return_value=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(404)))):
  assert call_tts.check()['state']=='unavailable'


def test_missing_voice_is_reported_without_provider_body_or_voice_fallback():
 os.environ.update(STELLIS_TTS_BACKEND='nebius',STELLIS_CHATTERBOX_NEBIUS_URL='https://tts.example',STELLIS_CHATTERBOX_NEBIUS_TOKEN='private-token',STELLIS_CHATTERBOX_VOICE='Elena.wav')
 def handle(req):
  assert json.loads(req.content)['voice']=='Elena.wav'
  return httpx.Response(400,json={'detail':"unknown voice 'Elena.wav'; secret server paths"})
 with patch.object(call_tts.httpx,'Client',return_value=httpx.Client(transport=httpx.MockTransport(handle))):
  with pytest.raises(call_tts.VoiceUnavailableError) as error:call_tts.speak('Check this voice')
 assert 'secret' not in str(error.value)


def test_nebius_default_matches_the_cookbook_emily_preset():
 os.environ.update(STELLIS_TTS_BACKEND="nebius",STELLIS_CHATTERBOX_NEBIUS_URL="https://tts.example",STELLIS_CHATTERBOX_NEBIUS_TOKEN="private-token")
 assert call_tts.status()["voice"]=="Emily.wav"
