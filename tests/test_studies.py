from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import threading
import time

import meshio
import numpy as np
import pytest

from kratos_mcp import jobs, kratos_env, studies, study_coordinator

EXAMPLE = Path(__file__).parents[1] / 'src/kratos_mcp/examples/cantilever'
AXIS = {'file': 'Materials.json', 'pointer': '/properties/0/Material/Variables/YOUNG_MODULUS',
        'values': [1.05e11, 2.1e11, 4.2e11]}
SPEC = {'kind': 'parameter', 'axes': [AXIS]}
RESPONSES = [{'name': 'tip', 'variable': 'DISPLACEMENT', 'point': [1, 0, 0], 'component': 1}]


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv('KRATOS_MCP_HOME', str(tmp_path / 'state'))
    env = kratos_env.KratosEnv(root=None, pythonpath=None, libs=None, source=None,
                               pip_installed=True, python=sys.executable)
    monkeypatch.setattr(kratos_env, 'resolve', lambda: env)
    monkeypatch.setattr(studies, '_launch', lambda path: None)
    case = tmp_path / 'case'
    shutil.copytree(EXAMPLE, case)
    return case


def start(case, spec=None, responses=None, **kwargs):
    return studies.start(str(case), copy.deepcopy(spec or SPEC), copy.deepcopy(responses or RESPONSES), **kwargs)


def command(job_id, script):
    manifest = jobs._job_dir(job_id) / 'manifest.json'
    data = json.loads(manifest.read_text())
    data['command'] = [sys.executable, '-c', script]
    manifest.write_text(json.dumps(data))


def coordinate(meta, background=False):
    path = studies.directory(meta['study_id'])
    data = studies.read(path)
    data.update(state='running', coordinator_pid=os.getpid())
    studies.write(path, data)
    if background:
        thread = threading.Thread(target=study_coordinator.run, args=(meta['study_id'], .01), daemon=True)
        thread.start()
        return thread
    study_coordinator.run(meta['study_id'], .01)
    return studies.status(meta['study_id'])


def await_condition(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    pytest.fail('condition did not become true')


def test_product_zip_and_pointers():
    axes = [{'file': 'a.json', 'pointer': '/a', 'values': [1, 2]},
            {'file': 'b.json', 'pointer': '/b', 'values': [3, 4]}]
    product = studies.expand({'axes': axes})
    assert [[o['value'] for o in row] for row in product] == [[1, 3], [1, 4], [2, 3], [2, 4]]
    assert len(studies.expand({'axes': axes, 'combination': 'zip'})) == 2
    document = {'a/b': {'~key': [1, 2]}}
    studies.replace(document, '/a~1b/~0key/0', False)
    assert document['a/b']['~key'] == [False, 2]
    for pointer in ['/missing', '/a~1b/~0key/2', '/a~1b/~0key/01', '/a~1b/~0key/-', 'a', '/bad~2']:
        with pytest.raises(ValueError):
            studies.replace(document, pointer, 0)
    with pytest.raises(ValueError, match='Duplicate'):
        studies.expand({'axes': [axes[0], axes[0]]})
    with pytest.raises(ValueError, match='overlapping'):
        studies.expand({'axes': [axes[0], {**axes[0], 'pointer': '/a/b'}]})
    with pytest.raises(ValueError, match='equal'):
        studies.expand({'axes': [axes[0], {**axes[1], 'values': [1]}], 'combination': 'zip'})
    for filename in ['../a.json', '/a.json', '']:
        with pytest.raises(ValueError):
            studies.expand({'axes': [{**axes[0], 'file': filename}]})


def test_prepared_snapshots_are_independent_and_verified(prepared):
    before = studies.inventory(prepared)
    meta = start(prepared)
    assert studies.inventory(prepared) == before
    assert len(meta['variants']) == 3
    snapshots = [jobs._job_dir(row['job_id']) / 'snapshot' for row in meta['variants']]
    assert [json.loads((p / 'Materials.json').read_text())['properties'][0]['Material']['Variables']['YOUNG_MODULUS']
            for p in snapshots] == AXIS['values']
    assert all(jobs.status(row['job_id'])['state'] == 'queued' for row in meta['variants'])
    (prepared / 'Materials.json').write_text('{}')
    studies.verify(meta, studies.directory(meta['study_id']))
    (snapshots[0] / 'extra').write_text('changed')
    with pytest.raises(RuntimeError, match='snapshot changed'):
        studies.resume(meta['study_id'])


@pytest.mark.parametrize('change', [
    {'axes': [{**AXIS, 'pointer': '/missing'}]},
    {'axes': [{**AXIS, 'file': '../outside.json'}]},
    {'axes': [{**AXIS, 'values': [float('nan')]}]},
])
def test_invalid_spec_launches_nothing(prepared, change):
    with pytest.raises((ValueError, FileNotFoundError)):
        start(prepared, {**SPEC, **change})
    assert jobs.list_jobs() == []
    assert studies.list_studies() == []


@pytest.mark.parametrize('override', [
    {'pointer': '/problem_data/parallel_type', 'values': ['MPI']},
    {'pointer': '/solver_settings/model_import_settings/input_type', 'values': ['rest']},
])
def test_unsupported_variant_launches_nothing(prepared, override):
    with pytest.raises(ValueError):
        start(prepared, {'kind': 'parameter', 'axes': [{'file': 'ProjectParameters.json', **override}]})
    assert jobs.list_jobs() == []


def test_all_variants_validated_before_jobs(prepared):
    spec = {'kind': 'parameter', 'axes': [{'file': 'ProjectParameters.json',
        'pointer': '/processes/constraints_process_list/0/Parameters/model_part_name',
        'values': ['Structure.left', 'Structure.nonexistent']}]}
    with pytest.raises(ValueError, match='Variant 1'):
        start(prepared, spec)
    assert jobs.list_jobs() == []


def test_mesh_levels_and_boundaries(prepared):
    spec = {'kind': 'mesh', 'mesh': {'kind': 'rectangle', 'size': [1, .2]},
            'divisions': [[4, 1], [8, 2], [16, 4]]}
    meta = start(prepared, spec)
    assert [row['mesh']['num_nodes'] for row in meta['variants']] == [10, 27, 85]
    from kratos_mcp import mdpa
    for row in meta['variants']:
        mesh = mdpa.read(jobs._job_dir(row['job_id']) / 'snapshot/mesh.mdpa')
        assert set(mesh.sub_model_parts) == {'domain', 'left', 'right', 'top', 'bottom'}
    for divisions in [[[4, 1], [4, 1]], [[4, 2], [8, 1]], [[4, 1], [0, 2]], [[4, 1], [8]]]:
        with pytest.raises(ValueError):
            studies.mesh_levels({**spec, 'divisions': divisions})


def test_external_inputs_frozen(prepared, tmp_path):
    external = tmp_path / 'external.mdpa'
    shutil.move(prepared / 'mesh.mdpa', external)
    params = prepared / 'ProjectParameters.json'
    data = json.loads(params.read_text())
    data['solver_settings']['model_import_settings']['input_filename'] = str(external.with_suffix(''))
    params.write_text(json.dumps(data))
    meta = start(prepared, external_inputs={str(external): 'mesh.mdpa'})
    external.write_text('changed')
    studies.verify(meta, studies.directory(meta['study_id']))


def test_bounded_concurrency_and_failure_isolation(prepared):
    meta = start(prepared, max_concurrency=2)
    events = prepared.parent / 'events'
    for index, row in enumerate(meta['variants']):
        command(row['job_id'], f"import time; from pathlib import Path; p=Path({str(events)!r}); "
                f"f=p.open('a'); f.write('start {index} '+str(time.time())+'\\n'); f.flush(); "
                f"time.sleep(.4); f.write('end {index} '+str(time.time())+'\\n'); f.close(); raise SystemExit({1 if index == 1 else 0})")
    result = coordinate(meta)
    assert result['state'] == 'completed_with_errors'  # Missing probe files are independent errors.
    assert [row['state'] for row in result['variants']] == ['succeeded', 'failed', 'succeeded']
    assert 'error' in result['variants'][0]['responses']['tip']
    active = peak = 0
    for line in events.read_text().splitlines():
        active += 1 if line.startswith('start') else -1
        peak = max(peak, active)
    assert peak == 2 and active == 0
    assert result['variants'][1]['collected']


def test_duplicate_supervisor_claim_and_cancel_queued(prepared):
    meta = start(prepared)
    row = meta['variants'][0]
    executions = prepared.parent / 'executions'
    command(row['job_id'], f"from pathlib import Path; import time; p=Path({str(executions)!r}); p.open('a').write('run\\n'); time.sleep(.2)")
    jobs.launch_prepared(row['job_id'])
    jobs.launch_prepared(row['job_id'])
    await_condition(lambda: jobs.status(row['job_id'])['state'] in jobs.TERMINAL_STATES)
    assert executions.read_text() == 'run\n'
    other = meta['variants'][1]['job_id']
    jobs.cancel(other)
    jobs.launch_prepared(other)
    assert jobs.status(other)['state'] == 'cancelled'


def test_cancellation_stops_queue_and_active_jobs(prepared):
    meta = start(prepared)
    for row in meta['variants']:
        command(row['job_id'], 'import time; time.sleep(30)')
    thread = coordinate(meta, background=True)
    await_condition(lambda: jobs.status(meta['variants'][0]['job_id'])['state'] == 'running')
    studies.cancel(meta['study_id'])
    thread.join(10)
    assert not thread.is_alive()
    result = studies.status(meta['study_id'])
    assert result['state'] == 'cancelled'
    assert all(row['job']['state'] == 'cancelled' for row in result['variants'])
    assert all(row['job']['started_at'] is None for row in result['variants'][1:])
    with pytest.raises(ValueError, match='Only interrupted'):
        studies.resume(meta['study_id'])


def test_response_reductions_and_distance(prepared):
    meta = start(prepared)
    row = meta['variants'][0]
    execution = Path(jobs.status(row['job_id'])['case_dir'])
    records = []
    for step, value in enumerate([-3., -1., -2.]):
        output = execution / f'{step}.vtk'
        meshio.write(output, meshio.Mesh([[0., 0., 0.], [1., 0., 0.]], [('line', [[0, 1]])],
                     point_data={'DISPLACEMENT': np.array([[0., 0., 0.], [0., value, 0.]])},
                     cell_data={'PRESSURE': [np.array([step + 1.])]}))
        records.append({'file': str(output), 'time': float(step)})
    (execution / 'result-index.json').write_text(json.dumps({'records': records}))
    response = studies.validate_responses(RESPONSES)[0]
    for reduction, expected in [('final', -2.), ('min', -3.), ('max', -1.)]:
        result = studies.extract(row['job_id'], {**response, 'reduction': reduction})
        assert result['value'] == expected
        assert len(result['samples']) == 3
        assert result['samples'][0]['coordinates'] == [1, 0, 0]
    assert studies.extract(row['job_id'], {**response, 'component': 'magnitude'})['value'] == 2.
    cell_response = {**response, 'variable': 'PRESSURE', 'association': 'cell', 'component': None, 'point': [.5, 0]}
    assert studies.extract(row['job_id'], cell_response)['value'] == 3.
    with pytest.raises(ValueError, match='max_distance'):
        studies.extract(row['job_id'], {**response, 'point': [5, 0], 'max_distance': .1})
    with pytest.raises(ValueError, match='require component'):
        studies.extract(row['job_id'], {**response, 'component': None})
    with pytest.raises(ValueError, match='out of range'):
        studies.extract(row['job_id'], {**response, 'component': 4})


def test_comparisons_and_csv(prepared, tmp_path):
    spec = {'kind': 'mesh', 'mesh': {'kind': 'rectangle', 'size': [1, .2]}, 'divisions': [[4, 1], [8, 2], [16, 4]]}
    meta = start(prepared, spec)
    path = studies.directory(meta['study_id'])
    data = studies.read(path)
    for row, value in zip(data['variants'], [1., 2., 3.]):
        row['responses'] = {'tip': {'value': value, 'samples': [{'time': 1.}]}}
    studies.write(path, data)
    output = tmp_path / 'responses.csv'
    result = studies.results(meta['study_id'], str(output))
    assert result['variants'][0]['comparisons']['tip']['finest']['absolute_difference'] == 2.
    assert result['variants'][2]['comparisons']['tip']['previous']['relative_difference'] == .5
    with pytest.raises(FileExistsError):
        studies.results(meta['study_id'], str(output))
    tolerances = {'atol': 1e-8, 'rtol': 1e-5, 'time_atol': 1e-12}
    reference = {'value': 0., 'samples': [{'time': 1.}]}
    assert studies.comparison(reference, reference, tolerances)['relative_difference'] is None
    candidate = {**reference, 'samples': [{'time': 2.}]}
    assert 'error' in studies.comparison(candidate, reference, tolerances)
    candidate = {**reference, 'samples': [{'time': 1.}, {'time': 1. + 1e-13}]}
    assert 'error' in studies.comparison(candidate, reference, tolerances)
    data['variants'][-1]['responses'] = {}
    studies.write(path, data)
    assert 'error' in studies.results(meta['study_id'])['variants'][0]['comparisons']['tip']['finest']


async def test_mcp_schemas_and_errors():
    from kratos_mcp.server import mcp
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    assert set(['study_start', 'study_status', 'study_list', 'study_cancel', 'study_resume', 'study_results']) <= set(tools)
    schema = tools['study_start'].inputSchema
    assert set(schema['required']) == {'case_dir', 'specification', 'responses'}
    assert schema['properties']['max_concurrency']['default'] == 1
    result = await mcp.call_tool('study_status', {'study_id': '../invalid'})
    assert 'Invalid study ID' in str(result)


def test_cancel_launch_race(prepared, monkeypatch):
    meta = start(prepared)
    real_launch = jobs.launch_prepared
    calls = []
    def racing_launch(job_id):
        calls.append(job_id)
        (studies.directory(meta['study_id']) / 'cancel.request').touch()
        jobs.cancel(job_id)
        real_launch(job_id)
    monkeypatch.setattr(jobs, 'launch_prepared', racing_launch)
    result = coordinate(meta)
    assert result['state'] == 'cancelled'
    assert len(calls) == 1
    assert all(row['job']['started_at'] is None for row in result['variants'])


def test_launch_failure_does_not_block_remaining_variants(prepared, monkeypatch):
    meta = start(prepared)
    real_launch = jobs.launch_prepared
    first = meta['variants'][0]['job_id']
    for row in meta['variants']:
        command(row['job_id'], 'pass')
    def launch(job_id):
        if job_id == first:
            raise OSError('fake launch failure')
        real_launch(job_id)
    monkeypatch.setattr(jobs, 'launch_prepared', launch)
    result = coordinate(meta)
    assert [row['state'] for row in result['variants']] == ['failed', 'succeeded', 'succeeded']
    assert result['variants'][0]['error'] == 'fake launch failure'


def test_interruption_and_resume_checks(prepared, monkeypatch):
    meta = start(prepared)
    path = studies.directory(meta['study_id'])
    stored = studies.read(path)
    stored.update(state='running', coordinator_started=True, coordinator_pid=os.getpid())
    studies.write(path, stored)
    # A released ownership lock proves the started coordinator is gone, even
    # if its process ID is still present (e.g. a zombie or reused ID).
    assert studies.status(meta['study_id'])['state'] == 'interrupted'
    studies.resume(meta['study_id'])  # Fixture suppresses spawning.
    env = kratos_env.resolve()
    monkeypatch.setattr(env, 'fingerprint', lambda: 'different-build')
    with pytest.raises(RuntimeError, match='fingerprint'):
        studies.resume(meta['study_id'])


def test_explicit_mdpa_suffix(prepared):
    params = prepared / 'ProjectParameters.json'
    data = json.loads(params.read_text())
    data['solver_settings']['model_import_settings']['input_filename'] = 'mesh.mdpa'
    params.write_text(json.dumps(data))
    assert len(start(prepared)['variants']) == 3


@pytest.mark.parametrize('changes', [{'name': ''}, {'point': [float('nan'), 0]},
    {'component': -1}, {'max_distance': -1}, {'association': 'face'}, {'reduction': 'average'},
    {'entity_index': 1}])
def test_invalid_responses_rejected(changes):
    with pytest.raises(ValueError):
        studies.validate_responses([{**RESPONSES[0], **changes}])


def test_supervisor_waits_for_status_reader(prepared):
    from kratos_mcp.job_supervisor import exclusive
    meta = start(prepared)
    job_id = meta['variants'][0]['job_id']
    command(job_id, 'pass')
    # A status reconciliation may temporarily hold this lock during startup.
    # It must delay the supervisor, not make the supervisor abandon its job.
    with exclusive(jobs._job_dir(job_id) / 'owner.lock'):
        jobs.launch_prepared(job_id)
        time.sleep(.1)
        assert jobs._live_procs[job_id].poll() is None
    await_condition(lambda: jobs.status(job_id)['state'] in jobs.TERMINAL_STATES)
    assert jobs.status(job_id)['state'] == 'succeeded'


def test_cancel_escalates_for_runner_ignoring_term(prepared):
    meta = start(prepared)
    job_id = meta['variants'][0]['job_id']
    receipt = prepared.parent / 'runner-pid'
    command(job_id, f"import signal,time,os; from pathlib import Path; "
            f"signal.signal(signal.SIGTERM, signal.SIG_IGN); Path({str(receipt)!r}).write_text(str(os.getpid())); time.sleep(30)")
    jobs.launch_prepared(job_id)
    await_condition(receipt.exists)
    runner_pid = int(receipt.read_text())
    assert jobs.cancel(job_id, grace_seconds=.1)['state'] == 'cancelled'
    await_condition(lambda: not jobs._pid_alive(runner_pid))
