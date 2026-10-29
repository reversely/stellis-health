"""A completed SSE reply must not wait for the HTTP connection to close."""
import unittest
from unittest.mock import patch
from stellis import intake


class ReplyStreamTest(unittest.IsolatedAsyncioTestCase):
    async def test_done_delivers_text_and_end_tool_without_reading_more_data(self):
        class Response:
            status_code = 200
            async def __aenter__(self):return self
            async def __aexit__(self, *args):pass
            async def aiter_lines(self):
                yield 'data: '
                yield 'data: {"choices":[{"delta":{"content":"Goodbye"}}]}'
                yield 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"close","function":{"name":"end_call","arguments":"{}"}}]}}]}'
                yield 'data:[DONE]'
                raise AssertionError('Reading after DONE would wait on a completed stream')
        class Client:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            def stream(self,*args,**kwargs):return Response()
        with patch('httpx.AsyncClient',return_value=Client()), patch.object(intake,'_key',return_value='synthetic-key'):
            pieces = [piece async for piece in intake.speakable(intake.stream_tokens([], [intake.END_CALL]))]
        self.assertIn('Goodbye',pieces)
        self.assertEqual(next(p for p in pieces if isinstance(p,dict))['name'],'end_call')
