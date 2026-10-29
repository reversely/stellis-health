"""A paused call stream can close from a different task without leaking its trace."""
import asyncio
import pytest
from stellis import intake,runtime_trace

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
 monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path))
 monkeypatch.setenv('STELLIS_RUNTIME_PROFILE','direct')

def test_cross_task_close_interrupts_producer_and_preserves_caller_context(monkeypatch):
 async def source(*args):
  assert runtime_trace.active()
  yield 'First clause.'
  await asyncio.Event().wait()
 monkeypatch.setattr(intake,'_stream_tokens',source)
 async def run():
  stream=intake.stream_tokens([])
  assert await anext(stream)=='First clause.'
  assert not runtime_trace.active()
  await asyncio.create_task(stream.aclose())
  assert not runtime_trace.active()
 asyncio.run(run())
 trace=runtime_trace.recent()[0]
 assert trace['state']=='interrupted' and trace['finished_at']

def test_completion_preserves_tokens_and_finishes_trace(monkeypatch):
 async def source(*args):
  yield 'First.'
  yield {'name':'tool','arguments':{}}
 monkeypatch.setattr(intake,'_stream_tokens',source)
 async def run():return [piece async for piece in intake.stream_tokens([])]
 assert asyncio.run(run())==['First.',{'name':'tool','arguments':{}}]
 assert runtime_trace.recent()[0]['state']=='succeeded'

def test_provider_error_is_propagated_and_trace_finishes(monkeypatch):
 async def source(*args):
  raise intake.IntakeError('Provider unavailable')
  yield
 monkeypatch.setattr(intake,'_stream_tokens',source)
 async def run():
  with pytest.raises(intake.IntakeError):
   async for piece in intake.stream_tokens([]):pass
 asyncio.run(run())
 assert runtime_trace.recent()[0]['state']=='failed'
