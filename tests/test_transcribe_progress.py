"""Whisper's progress moves the job bar between 12 and 55 percent without rewriting it per passage."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from stellis import asr, processing


class TranscribeProgressTest(unittest.TestCase):
    def test_progress_is_saved_in_steps_of_two_percent(self) -> None:
        with patch.object(processing.storage, "update_job") as update:
            report = processing._transcribe_progress("job")
            for fraction in (0.01, 0.03, 0.05, 0.5, 0.51, 1.0):
                report(fraction)
        saved = [call.kwargs["progress"] for call in update.call_args_list]
        self.assertEqual(saved, [0.141, 0.335, 0.55])

    def test_passages_report_how_much_audio_is_decoded(self) -> None:
        seen = []

        class Model:
            _ctx = None

            def transcribe(self, audio, new_segment_callback, **params):
                self.threads = params["n_threads"]
                new_segment_callback(SimpleNamespace(t1=500))  # 5 s of a 10 s clip

        model = Model()
        native = SimpleNamespace(whisper_full_n_segments=lambda context: 0)
        token = asr.PROGRESS.set(seen.append)
        try:
            with patch.object(asr, "_model", return_value=model), \
                    patch.dict("sys.modules", {"_pywhispercpp": native}):
                asr.transcribe(np.zeros(160000, dtype=np.float32), None)
        finally:
            asr.PROGRESS.reset(token)
        self.assertEqual(seen, [0.5])
        self.assertGreaterEqual(model.threads, 1)


if __name__ == "__main__":
    unittest.main()
