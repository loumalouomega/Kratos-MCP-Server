"""Durable study-job ownership. No native libraries are imported here.

A supervisor leads the process group and holds an OS lock until its runner
exits. Duplicate launch attempts cannot execute the same prepared job twice.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from . import jobs


@contextmanager
def exclusive(path: Path, *, blocking: bool = False):
    import fcntl
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def reconcile(directory: Path) -> jobs.JobMeta:
    try:
        with exclusive(directory / 'owner.lock'):
            meta = jobs._read_meta(directory)
            if meta.state not in jobs.TERMINAL_STATES:
                if (directory / 'cancel.request').exists():
                    meta.state = 'cancelled'
                elif meta.state == 'running':
                    # Owner died without publishing an exit status. Never rerun
                    # this identity, even if it failed before starting Kratos.
                    meta.state = 'failed'
                    meta.extra['supervisor_error'] = 'Job supervisor exited without an exit status'
                    # Its runner might still be alive in the orphaned group.
                    if meta.pid:
                        try:
                            os.killpg(meta.pid, 9)
                        except ProcessLookupError:
                            pass
                if meta.state in jobs.TERMINAL_STATES:
                    meta.finished_at = time.time()
                    jobs._write_meta(directory, meta)
            return meta
    except BlockingIOError:
        return jobs._read_meta(directory)


def run(job_id: str) -> int:
    # Group SIGTERM also reaches the runner. Keep its parent alive to reap it
    # and to let jobs.cancel escalate when the runner ignores SIGTERM.
    signal.signal(signal.SIGTERM, lambda *_: None)
    directory = jobs._job_dir(job_id)
    try:
        with exclusive(directory / 'owner.lock', blocking=True):
            meta = jobs._read_meta(directory)
            if meta.state != 'queued':
                return 0
            if (directory / 'cancel.request').exists():
                meta.state = 'cancelled'
                meta.finished_at = time.time()
                jobs._write_meta(directory, meta)
                return 0
            meta.state = 'running'
            meta.pid = os.getpid()
            meta.started_at = time.time()
            jobs._write_meta(directory, meta)
            try:
                manifest = json.loads((directory / 'manifest.json').read_text())
                if (directory / 'cancel.request').exists():
                    code = -15
                else:
                    code = subprocess.call(manifest['command'], stdin=subprocess.DEVNULL)
            except Exception as exc:
                meta.extra['launch_error'] = str(exc)
                code = 1
            meta.returncode = code
            meta.state = ('cancelled' if (directory / 'cancel.request').exists()
                          else 'succeeded' if code == 0 else 'failed')
            meta.finished_at = time.time()
            jobs._write_meta(directory, meta)
            return code
    except BlockingIOError:
        return 0


if __name__ == '__main__':
    sys.exit(run(sys.argv[1]))
