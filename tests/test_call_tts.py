"""Speech adapter contract and credential boundaries."""

import io
import os
import unittest
from unittest import mock

import httpx
import numpy as np
import soundfile as sf

from stellis import call_tts, intake_voice


class CallTtsTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(os.environ, {}, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_cpu_is_default_and_unknown_backend_is_rejected(self):
        self.assertEqual(call_tts.backend(), "cpu")
        os.environ["STELLIS_TTS_BACKEND"] = "typo"
        with self.assertRaises(ValueError):
            call_tts.backend()

    def test_nebius_requires_https_and_token_before_request(self):
        os.environ["STELLIS_TTS_BACKEND"] = "nebius"
        for url in ("http://example.com", "https://example.com?token=secret",
                    "https://user:secret@example.com", "https://example.com"):
            os.environ["STELLIS_CHATTERBOX_NEBIUS_URL"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                call_tts.speak("Hello")

    def test_wav_becomes_pcm_and_token_is_only_in_header(self):
        os.environ.update(STELLIS_TTS_BACKEND="nebius",
                          STELLIS_CHATTERBOX_NEBIUS_URL="https://example.com",
                          STELLIS_CHATTERBOX_NEBIUS_TOKEN="test-token")
        buf = io.BytesIO()
        sf.write(buf, np.zeros(2400), 24000, format="WAV")

        def handle(request):
            self.assertEqual(str(request.url), "https://example.com/v1/audio/speech")
            self.assertEqual(request.headers["authorization"], "Bearer test-token")
            self.assertNotIn(b"test-token", request.content)
            return httpx.Response(200, content=buf.getvalue())

        client = httpx.Client(transport=httpx.MockTransport(handle))
        with mock.patch.object(call_tts.httpx, "Client", return_value=client):
            pcm, rate = call_tts.speak("Hello")
        self.assertEqual((len(pcm), rate), (4800, 24000))

    def test_redirect_and_error_body_are_not_exposed(self):
        os.environ["STELLIS_TTS_BACKEND"] = "local"
        for status in (302, 401, 503):
            client = httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(status, text="sensitive provider response")))
            with mock.patch.object(call_tts.httpx, "Client", return_value=client):
                with self.assertRaisesRegex(RuntimeError, f"HTTP {status}") as error:
                    call_tts.speak("Hello")
            self.assertNotIn("sensitive", str(error.exception))

    def test_stereo_audio_is_rejected(self):
        os.environ["STELLIS_TTS_BACKEND"] = "local"
        buf = io.BytesIO()
        sf.write(buf, np.zeros((24, 2)), 24000, format="WAV")
        client = httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=buf.getvalue())))
        with mock.patch.object(call_tts.httpx, "Client", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "mono WAV"):
                call_tts.speak("Hello")

    def test_response_limit_is_enforced(self):
        os.environ["STELLIS_TTS_BACKEND"] = "local"
        client = httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"12345")))
        with (mock.patch.object(call_tts.httpx, "Client", return_value=client),
              mock.patch.object(call_tts, "MAX_AUDIO_BYTES", 4)):
            with self.assertRaisesRegex(RuntimeError, "response limit"):
                call_tts.speak("Hello")

    def test_engine_routes_only_assistant_voice_to_chatterbox(self):
        os.environ["STELLIS_TTS_BACKEND"] = "local"
        engine = intake_voice.Engine.__new__(intake_voice.Engine)
        with mock.patch.object(call_tts, "speak", return_value=(b"pcm", 24000)) as speech:
            self.assertEqual(engine.speak("Hello"), (b"pcm", 24000))
        speech.assert_called_once_with("Hello")


    def test_kokoro_speaks_the_assistant_and_kitten_the_simulated_patient(self):
        os.environ["STELLIS_TTS_BACKEND"] = "kokoro"
        self.assertEqual(call_tts.status()["voice"], "bf_emma")
        self.assertIn(intake_voice.KOKORO[0], intake_voice.missing() or [intake_voice.KOKORO[0]])
        engine = intake_voice.Engine.__new__(intake_voice.Engine)
        engine._lock = mock.MagicMock()
        engine.kokoro, engine.tts = mock.Mock(), mock.Mock()
        engine.kokoro.generate.return_value = mock.Mock(samples=[0.0, 0.5], sample_rate=24000)
        engine.tts.generate.return_value = mock.Mock(samples=[0.0], sample_rate=24000)
        with mock.patch.object(call_tts, "speak") as chatterbox:
            engine.speak("Hello")
            engine.speak("I am the patient", voice=intake_voice.DENVER_VOICE)
        chatterbox.assert_not_called()
        engine.kokoro.generate.assert_called_once_with("Hello", sid=intake_voice.KOKORO_VOICE, speed=1.0)
        engine.tts.generate.assert_called_once_with("I am the patient", sid=intake_voice.DENVER_VOICE, speed=1.0)


class SpeechFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_speech_keeps_text_and_reports_error(self):
        send_json = mock.AsyncMock()
        send_bytes = mock.AsyncMock()
        engine = mock.Mock()
        engine.speak.side_effect = RuntimeError("private endpoint details")
        call = intake_voice.Call(send_json, send_bytes, engine)
        await call._say("Hello", 0)
        messages = [args.args[0] for args in send_json.call_args_list]
        self.assertEqual(messages[0]["type"], "error")
        self.assertNotIn("private", messages[0]["message"])
        self.assertEqual(messages[1]["text"], "Hello")
        self.assertFalse(messages[1]["audio"])
        send_bytes.assert_not_called()
