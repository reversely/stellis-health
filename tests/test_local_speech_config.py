"""Filesystem-only validation; real model execution is verified on GN100."""
import os
import tempfile
import unittest
from pathlib import Path
from scripts.configure_local_speech import configure


class LocalSpeechConfigTest(unittest.TestCase):
    def test_incomplete_assets_leave_existing_service_settings_untouched(self):
        with tempfile.TemporaryDirectory() as data:
            root=Path(data);env=root/'service.env';env.write_text('EXISTING_SETTING=keep\n')
            with self.assertRaises(ValueError):configure(env,root/'missing',root,root,root)
            self.assertEqual(env.read_text(),'EXISTING_SETTING=keep\n')

    def test_update_is_private_idempotent_and_preserves_unrelated_settings(self):
        with tempfile.TemporaryDirectory() as data:
            root=Path(data);worker=root/'worker';worker.write_text('test file');worker.chmod(0o700)
            for name in ['tokens.txt','encoder.int8.onnx','decoder.int8.onnx','joiner.int8.onnx','ggml-small.en.bin','pyannote-segmentation-3.0-int8.onnx','nemo-en-titanet-small.onnx']:(root/name).write_bytes(b'asset presence')
            env=root/'service.env';env.write_text('EXISTING_SETTING=keep\nSTELLIS_AUDIO_WORKER=old\n')
            configure(env,worker,root,root,root);first=env.read_text();configure(env,worker,root,root,root)
            self.assertEqual(env.read_text(),first);self.assertIn('EXISTING_SETTING=keep',first)
            self.assertEqual(first.count('STELLIS_AUDIO_WORKER='),1);self.assertEqual(env.stat().st_mode & 0o777,0o600)
