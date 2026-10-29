"""Isolated native speech runner so ONNX inference cannot block HTTP polling."""

from __future__ import annotations

import fcntl
import os
import signal
import sys
import threading
import time
import uuid
from pathlib import Path

from stellis import processing, storage


def main() -> None:
    if len(sys.argv) not in (2, 3):
        raise SystemExit("Expected one job ID and an optional demo workspace")
    job_id = str(uuid.UUID(sys.argv[1]))
    if len(sys.argv) == 3:
        # A job queued inside a demo runs against the demo's own database.
        with storage.demo_workspace(Path(sys.argv[2])):
            _run(job_id)
    else:
        _run(job_id)


def _run(job_id: str) -> None:
    # SIGTERM unwinds normally so temporary audio folders are removed.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    parent = os.getppid()

    def exit_with_parent() -> None:
        while os.getppid() == parent:
            time.sleep(1)
        os.killpg(os.getpgid(0), signal.SIGTERM)

    threading.Thread(target=exit_with_parent, name="parent-watch", daemon=True).start()
    lock_dir = storage.data_dir() / "job-locks"
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    lock_path = lock_dir / f"{job_id}.lock"
    with lock_path.open("a+b") as lock:
        lock_path.chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        processing.run_job(job_id)


if __name__ == "__main__":
    main()
