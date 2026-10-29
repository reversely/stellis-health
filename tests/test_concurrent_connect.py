"""First connections racing on a new data directory must all succeed (#192)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from stellis import auth, storage

COUNT = 12
CHILD = "from stellis import auth, storage\nwith storage.connect(): pass\nwith auth._connect(): pass\n"


class ConcurrentConnectTest(unittest.TestCase):
    def test_many_first_connections_to_a_new_data_directory_all_succeed(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"STELLIS_DATA_DIR": folder}):
            gate, errors = threading.Barrier(COUNT), []

            def open_both() -> None:
                try:
                    gate.wait()
                    with storage.connect(), auth._connect():
                        pass
                except Exception as error:  # Collected so the assertion names every failure.
                    errors.append(repr(error))

            threads = [threading.Thread(target=open_both) for _ in range(COUNT)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
            self.assertEqual(errors, [])

        # The speech worker is a separate process, so race processes too, on a second fresh directory.
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, "STELLIS_DATA_DIR": folder, "PYTHONPATH": str(Path(storage.__file__).parents[1])}
            children = [subprocess.Popen([sys.executable, "-c", CHILD], env=env, stderr=subprocess.PIPE, text=True)
                        for _ in range(6)]
            failures = [child.communicate(timeout=120)[1] for child in children if child.wait(120) != 0]
            self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
