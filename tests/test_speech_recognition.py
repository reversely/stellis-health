"""Real on-device recognition through the call pipeline: fixture audio in, heard text and answers out.

Needs the call models (Moonshine, Silero, Kitten). Set STELLIS_CALL_MODELS to a folder where the app's
installer has put them; without them the tests skip. Fixtures come from scripts/speech_fixtures.py.
"""
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from stellis import auth, intake_voice

FIXTURES = Path(__file__).parent / "fixtures" / "speech"
FRAME = 2048  # Samples per microphone frame, as the browser sends them.


def frames(name: str):
    with wave.open(str(FIXTURES / f"{name}.wav")) as audio:
        assert audio.getframerate() == intake_voice.SAMPLE_RATE and audio.getnchannels() == 1
        data = audio.readframes(audio.getnframes())
    return [data[i:i + FRAME * 2] for i in range(0, len(data), FRAME * 2)]


@unittest.skipUnless(os.environ.get("STELLIS_CALL_MODELS") and not intake_voice.missing(),
                     "Set STELLIS_CALL_MODELS to installed call models")
class SpeechRecognitionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = intake_voice.Engine(intake_voice.directory())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name, "STELLIS_AUTH_DB_DIR": self.temp.name})
        env.start(); self.addCleanup(env.stop)
        auth.register("speech-user", "speech-test-password")

    def test_the_recognizer_hears_each_fixture(self):
        for name, words in (("name", ["my", "name", "alex"]), ("gender", ["woman"]),
                            ("conditions", ["tired", "december", "blood", "tests"])):
            with wave.open(str(FIXTURES / f"{name}.wav")) as audio:
                samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768
            heard = self.engine.transcribe(samples).lower()
            with self.subTest(fixture=name, heard=heard):
                for word in words:
                    self.assertIn(word, heard)


if __name__ == "__main__":
    unittest.main()
