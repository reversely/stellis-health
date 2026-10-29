"""Stopping the server stops the speech worker and the tools it started (#206)."""

from __future__ import annotations

import os
import fcntl
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch
import subprocess
import sys
import time
import unittest

from stellis import processing, storage


class WorkerShutdownTest(unittest.TestCase):
    def test_stop_worker_ends_the_whole_process_group(self) -> None:
        # A stand-in worker that starts a long-running child, like whisper-cli.
        script = "import subprocess, time; subprocess.Popen(['sleep', '60']); time.sleep(60)"
        child = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
        time.sleep(0.5)
        processing._CHILD = child
        try:
            processing.stop_worker()
            self.assertIsNotNone(child.poll())
            leftovers = subprocess.run(["pgrep", "-g", str(child.pid)], capture_output=True, text=True).stdout.split()
            self.assertEqual(leftovers, [])
        finally:
            processing._CHILD = None
            processing._STOPPING.clear()
            try:
                os.killpg(child.pid, 9)
            except ProcessLookupError:
                pass

    def test_idle_shutdown_joins_and_restart_ignores_old_wakeups(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STELLIS_DATA_DIR": directory}):
            try:
                processing.start_worker()
                first = processing._WORKER
                processing.stop_worker()
                self.assertFalse(first.is_alive())
                processing._QUEUE.put((str(uuid.uuid4()), None))
                processing.start_worker()
                self.assertIsNot(processing._WORKER, first)
                self.assertFalse(processing._STOPPING.is_set())
                processing.stop_worker()
                self.assertFalse((Path(directory) / "job-locks").exists())
            finally:
                processing.stop_worker()

    def test_active_real_worker_stops_and_durable_job_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STELLIS_DATA_DIR": directory}):
            conversation_id = str(uuid.uuid4())
            storage.add_conversation({"id": conversation_id, "title": "Shutdown check",
                "source_sha256": conversation_id, "audio_filename": "missing.wav",
                "audio_media_type": "audio/wav", "duration_ms": 1, "created_at": processing.now()})
            job = processing.request_processing(conversation_id)
            lock_dir = Path(directory) / "job-locks"
            lock_dir.mkdir()
            try:
                # Hold the real worker's durable lock so shutdown interrupts a live process
                # without requiring a model download or substituting an inference adapter.
                with (lock_dir / f"{job['id']}.lock").open("a+b") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    processing.start_worker()
                    deadline = time.monotonic() + 10
                    while processing._CHILD is None and time.monotonic() < deadline:
                        time.sleep(.01)
                    child = processing._CHILD
                    self.assertIsNotNone(child)
                    dispatcher = processing._WORKER
                    processing.stop_worker()
                    self.assertFalse(dispatcher.is_alive())
                    self.assertIsNotNone(child.poll())
                    self.assertEqual(storage.get_job(job["id"])["state"], "queued")
                processing.start_worker()
                deadline = time.monotonic() + 15
                while storage.get_job(job["id"])["state"] != "failed" and time.monotonic() < deadline:
                    time.sleep(.05)
                # The real worker resumed the job and reported its missing local inputs.
                self.assertEqual(storage.get_job(job["id"])["state"], "failed")
            finally:
                processing.stop_worker()

    def test_worker_exits_when_its_parent_disappears(self) -> None:
        # The parent shell exits immediately, orphaning the worker it launched.
        code = ("import os, signal, sys, threading, time\n"
                "from stellis import worker\n"
                "sys.argv = ['worker', '00000000-0000-0000-0000-000000000000']\n"
                "worker.processing.run_job = lambda job_id: time.sleep(60)\n"
                "worker.main()\n")
        # The launcher starts the worker in its own group, as the server does, then exits.
        launch = ("import subprocess, sys, time\n"
                  "child = subprocess.Popen([sys.executable, '-c', sys.argv[1]], start_new_session=True,\n"
                  "                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                  "print(child.pid, flush=True); time.sleep(1.5)\n")
        launcher = subprocess.run([sys.executable, "-c", launch, code], capture_output=True, text=True, timeout=30)
        pid = int(launcher.stdout.strip())
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.2)
        else:
            os.kill(pid, 9)
            self.fail("orphaned worker kept running")


if __name__ == "__main__":
    unittest.main()
