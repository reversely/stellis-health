"""Fixed call endings and cache boundaries."""
import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from stellis import call_audio_cache, call_lifecycle, tour


class EndIntentTest(unittest.TestCase):
    def test_explicit_endings_and_health_statements_stay_distinct(self):
        for text in ["That's all", 'Nothing else thanks', 'Please end the call', "I'm done", 'Goodbye', "No, that's all", 'That is all, goodbye']:
            self.assertTrue(call_lifecycle.wants_end(text), text)
        for text in ['I want to stop smoking', "I'm done with chemotherapy", 'Thanks for explaining', 'Nothing else helps my pain']:
            self.assertFalse(call_lifecycle.wants_end(text), text)


class NarrationCacheTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'STELLIS_DATA_DIR':self.temp.name,'STELLIS_TTS_BACKEND':'cpu'})
        self.env.start(); self.addCleanup(self.env.stop)

    def test_registered_lines_reuse_audio_and_voice_revision_invalidates_it(self):
        text = tour.STEPS[0].line
        key = tour.speech_cache_key(text)
        synthesize = Mock(return_value=(b'\0\0'*800, 16000))
        first = call_audio_cache.speak(key,text,synthesize)
        self.assertEqual(call_audio_cache.speak(key,text,synthesize),first)
        synthesize.assert_called_once_with(text)
        with patch.dict(os.environ, {'STELLIS_TTS_CACHE_REVISION':'next'}):
            call_audio_cache.speak(key,text,synthesize)
        self.assertEqual(synthesize.call_count,2)

    def test_questions_and_personal_goals_are_not_cacheable(self):
        self.assertIsNone(tour.speech_cache_key('How do I manage my migraine?'))
        synthesize = Mock(return_value=(b'\0\0',16000))
        call_audio_cache.speak(None,'My private answer',synthesize)
        self.assertFalse(os.listdir(self.temp.name))

    def test_corrupt_cache_regenerates_and_keys_cannot_escape_directory(self):
        text = tour.STEPS[0].line; key = tour.speech_cache_key(text)
        synthesize = Mock(return_value=(b'\0\0'*800,16000))
        call_audio_cache.speak(key,text,synthesize)
        path = call_audio_cache._path(key);path.write_bytes(b'not a wave file')
        call_audio_cache.speak(key,text,synthesize)
        self.assertEqual(synthesize.call_count,2)
        with self.assertRaises(ValueError):call_audio_cache.speak('../../escape',text,synthesize)

    def test_explicit_intake_end_does_not_wait_for_a_model(self):
        import asyncio
        from stellis import intake
        intake.opening(); intake.add_person_turn("No, that's all")
        async def collect():
            return [piece async for piece in intake.reply()]
        with patch.object(intake, 'stream_tokens', side_effect=AssertionError('No model needed')):
            pieces = asyncio.run(collect())
        self.assertEqual(pieces[-1], intake.END_MARK)

    def test_concurrent_completion_creates_one_saved_note(self):
        from concurrent.futures import ThreadPoolExecutor
        from stellis import intake, pages
        intake.opening(); intake.add_person_turn('I want to organize my appointments')
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda _:intake.end(), range(8)))
        self.assertEqual(len({result['page_id'] for result in results}), 1)
        self.assertEqual(len(pages.list_pages()), 1)
