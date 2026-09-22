"""Detached bounded scheduler; all simulations remain ordinary isolated jobs."""
from __future__ import annotations

import sys
import time

from . import jobs, studies
from .job_supervisor import exclusive


def run(study_id: str, poll_seconds: float = 0.2) -> None:
    path = studies.directory(study_id)
    with exclusive(path / 'owner.lock', blocking=True):
        meta = studies.read(path)
        if meta['state'] != 'running':
            return
        meta['coordinator_started'] = True
        studies.write(path, meta)
        launched = set()
        try:
            studies.verify(meta, path)
            while True:
                cancelling = (path / 'cancel.request').exists()
                active = 0
                for row in meta['variants']:
                    job_id = row['job_id']
                    proc = jobs._live_procs.get(job_id)
                    exited = proc is not None and proc.poll() is not None
                    child = jobs.refresh(job_id)
                    if cancelling and child.state not in jobs.TERMINAL_STATES:
                        jobs.cancel(job_id)
                        child = jobs.refresh(job_id)
                    if exited and child.state == 'queued':
                        child.state = 'failed'
                        child.finished_at = time.time()
                        child.extra['launch_error'] = 'Supervisor exited before claiming job'
                        jobs._write_meta(jobs._job_dir(job_id), child)
                    studies.collect(row, child, meta['responses'])
                    if child.state == 'running' or (child.state == 'queued' and job_id in launched):
                        active += 1
                if all(row['state'] in jobs.TERMINAL_STATES for row in meta['variants']):
                    errors = any(row['state'] != 'succeeded' or any('error' in response for response in row['responses'].values())
                                 for row in meta['variants'])
                    meta.update(state='cancelled' if cancelling else 'completed_with_errors' if errors else 'succeeded',
                                finished_at=time.time())
                    studies.write(path, meta)
                    return
                studies.write(path, meta)
                for row in meta['variants']:
                    if active >= meta['max_concurrency'] or (path / 'cancel.request').exists():
                        break
                    if row['state'] != 'queued' or row['job_id'] in launched:
                        continue
                    job_id = row['job_id']
                    try:
                        child_path = jobs._job_dir(job_id)
                        if studies.inventory(child_path / 'snapshot') != row['input_hashes'] or studies.inventory(child_path / 'execution') != row['input_hashes']:
                            raise RuntimeError('Prepared variant inputs changed before launch')
                        jobs.launch_prepared(job_id)
                        launched.add(job_id)
                        active += 1
                    except Exception as exc:
                        child = jobs._read_meta(jobs._job_dir(job_id))
                        child.state = 'failed'
                        child.finished_at = time.time()
                        child.extra['launch_error'] = str(exc)
                        jobs._write_meta(jobs._job_dir(job_id), child)
                time.sleep(poll_seconds)
        except Exception as exc:
            meta.update(state='interrupted', coordinator_error=str(exc))
            studies.write(path, meta)
            raise


if __name__ == '__main__':
    run(sys.argv[1])
