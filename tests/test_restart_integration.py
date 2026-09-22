"""Numerical acceptance test for managed checkpoint and result workflows."""
import json
import time
from pathlib import Path

import pytest

from kratos_mcp import checkpoints, jobs, mdpa, result_series
from kratos_mcp.tools import scaffold

pytestmark = pytest.mark.kratos


def wait(meta):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        status = jobs.status(meta.job_id)
        if status['state'] in jobs.TERMINAL_STATES:
            assert status['state'] == 'succeeded', jobs.logs(meta.job_id, tail=60)
            return
        time.sleep(.02)
    jobs.cancel(meta.job_id)
    pytest.fail('Job timed out')


def test_transient_restart_matches_reference(tmp_path, monkeypatch):
    monkeypatch.setenv('KRATOS_MCP_HOME', str(tmp_path / 'state'))
    from kratos_mcp import bridge
    apps = bridge.run_op('list_applications', use_cache=False)
    required = {'StructuralMechanicsApplication', 'LinearSolversApplication'}
    if not required.issubset(apps):
        pytest.skip(f'Missing optional applications: {sorted(required - set(apps))}')
    case = tmp_path / 'case'
    case.mkdir()
    values = scaffold._resolve_values('structural_dynamic', {'end_time': 100., 'time_step': .001})
    for name in ('ProjectParameters.json', 'Materials.json'):
        (case / name).write_text(scaffold.render_template_file('structural_dynamic', name, values))
    mdpa.create_rectangle_mesh(1., .2, 8, 2).write(case / 'mesh.mdpa')
    params = json.loads((case / 'ProjectParameters.json').read_text())
    params['processes']['loads_process_list'].append(scaffold._direction_to_conditions_process(
        'Structure.right', 'LINE_LOAD', 1e4, [0., -1., 0.], [0., 'End']))
    (case / 'ProjectParameters.json').write_text(json.dumps(params))
    checkpoints.configure(str(case), 5, 'step', 3)
    source = jobs.start(str(case), isolate=True)
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            rows = checkpoints.list_checkpoints(source.job_id)['checkpoints']
            if any(r['available'] for r in rows):
                break
            assert jobs.status(source.job_id)['state'] not in jobs.TERMINAL_STATES, jobs.logs(source.job_id)
            time.sleep(.01)
        else:
            pytest.fail('No checkpoint written')
    finally:
        jobs.cancel(source.job_id)
    rows = checkpoints.list_checkpoints(source.job_id)['checkpoints']
    checkpoint = [r for r in rows if r['available']][-1]
    source_hash = jobs._sha256(Path(checkpoint['path']))
    end = checkpoint['time'] + .02
    resumed = checkpoints.resume(source.job_id, checkpoint['path'], end)
    wait(resumed)
    assert Path(resumed.case_dir) != Path(source.case_dir)
    assert resumed.extra['resume_of'] == source.job_id
    assert jobs._sha256(Path(checkpoint['path'])) == source_hash
    params['problem_data']['end_time'] = end
    (case / 'ProjectParameters.json').write_text(json.dumps(params))
    reference = jobs.start(str(case), isolate=True)
    wait(reference)
    ref_rows = result_series.records(str(Path(reference.case_dir) / 'result-index.json'))
    resumed_rows = result_series.records(str(Path(resumed.case_dir) / 'result-index.json'))
    assert resumed_rows[0]['time'] > checkpoint['time']
    assert resumed_rows[0]['step'] > checkpoint['step']
    result = result_series.compare([ref_rows[-1]], [resumed_rows[-1]], 'DISPLACEMENT', atol=1e-10, rtol=1e-5)
    assert result['passed'], result
    history = result_series.history(resumed_rows, 'DISPLACEMENT', point=[1., .1, 0.])
    assert len(history['samples']) == len(resumed_rows)
    assert history['samples'][-1]['time'] == resumed_rows[-1]['time']
    rerun = jobs.rerun(resumed.job_id)
    wait(rerun)
    later = [r for r in checkpoints.list_checkpoints(resumed.job_id)['checkpoints'] if r['available']][-1]
    again = checkpoints.resume(resumed.job_id, later['path'], later['time'] + .005)
    wait(again)
