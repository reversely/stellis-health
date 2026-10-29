"""Model setup verifies downloads and removes staged files on failure."""

import hashlib
import io
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from stellis import live_asr_models as installer


class LiveAsrInstallerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.files = {name: f"sample {name}".encode() for name in installer.PARAKEET_FILES}
        self.digests = {name: (len(data), hashlib.sha256(data).hexdigest())
                        for name, data in self.files.items()}

    def _offline_archive(self) -> bytes:
        name = installer.MODELS[1][0]
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:bz2") as bundle:
            for choices in installer.REQUIRED_FILES[name]:
                data = b"sample model file"
                info = tarfile.TarInfo(f"{name}/{choices[0]}")
                info.size = len(data)
                bundle.addfile(info, io.BytesIO(data))
        return output.getvalue()

    def test_verified_models_install_and_leave_no_staging_copy(self) -> None:
        archive = self._offline_archive()
        models = ((installer.STREAMING_PARAKEET, None),
                  (installer.MODELS[1][0], hashlib.sha256(archive).hexdigest()))

        def download(url: str, timeout: int) -> io.BytesIO:
            if url.endswith(".tar.bz2"):
                self.assertEqual(timeout, 90)
                return io.BytesIO(archive)
            self.assertEqual(timeout, 180)
            return io.BytesIO(self.files[url.rsplit("/", 1)[-1]])

        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            with patch.object(installer, "MODELS", models), \
                 patch.object(installer, "PARAKEET_FILES", self.digests), \
                 patch.object(installer.shutil, "disk_usage",
                              return_value=SimpleNamespace(free=6 * 1024**3)), \
                 patch.object(installer.urllib.request, "urlopen", side_effect=download) as calls:
                with redirect_stdout(io.StringIO()):
                    installer.install(root)
                self.assertEqual(calls.call_count, 5)
                self.assertEqual({item.name for item in root.iterdir()}, {name for name, _ in models})
                self.assertTrue(installer._complete(installer.STREAMING_PARAKEET, root / installer.STREAMING_PARAKEET))
                calls.reset_mock()
                with redirect_stdout(io.StringIO()):
                    installer.install(root)
                calls.assert_not_called()

    def test_bad_streaming_checksum_removes_staged_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            with patch.object(installer, "PARAKEET_FILES", {"encoder.int8.onnx": (1, "0" * 64)}), \
                 patch.object(installer.shutil, "disk_usage",
                              return_value=SimpleNamespace(free=6 * 1024**3)), \
                 patch.object(installer.urllib.request, "urlopen", return_value=io.BytesIO(b"wrong")):
                with self.assertRaisesRegex(RuntimeError, "Model checksum failed"):
                    with redirect_stdout(io.StringIO()):
                        installer.install(root)
            self.assertEqual(list(root.iterdir()), [])

    def test_bad_archive_checksum_removes_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            streaming = root / installer.STREAMING_PARAKEET
            streaming.mkdir()
            for name, data in self.files.items():
                (streaming / name).write_bytes(data)
            with patch.object(installer, "PARAKEET_FILES", self.digests), \
                 patch.object(installer, "MODELS", ((installer.STREAMING_PARAKEET, None),
                                                    (installer.MODELS[1][0], "0" * 64))), \
                 patch.object(installer.shutil, "disk_usage",
                              return_value=SimpleNamespace(free=6 * 1024**3)), \
                 patch.object(installer.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")):
                with self.assertRaisesRegex(RuntimeError, "Model checksum failed"):
                    with redirect_stdout(io.StringIO()):
                        installer.install(root)
            self.assertEqual({item.name for item in root.iterdir()}, {installer.STREAMING_PARAKEET})

    def test_partial_directory_is_rejected_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            partial = root / installer.STREAMING_PARAKEET
            partial.mkdir()
            (partial / "tokens.txt").write_text("sample")
            with patch.object(installer.urllib.request, "urlopen",
                              side_effect=AssertionError("unexpected download")):
                with self.assertRaisesRegex(RuntimeError, "Incomplete model directory"):
                    with redirect_stdout(io.StringIO()):
                        installer.install(root)

    def test_low_space_is_rejected_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            with patch.object(installer.shutil, "disk_usage", return_value=SimpleNamespace(free=1024)), \
                 patch.object(installer.urllib.request, "urlopen",
                              side_effect=AssertionError("unexpected download")):
                with self.assertRaisesRegex(RuntimeError, "3 GiB free"):
                    with redirect_stdout(io.StringIO()):
                        installer.install(Path(temp_name))


if __name__ == "__main__":
    unittest.main()
