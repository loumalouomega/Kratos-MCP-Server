"""Numerical acceptance and detached coordinator recovery against the local build."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import signal
import sys
import subprocess
import time

import pytest

from kratos_mcp import jobs, kratos_env, studies

pytestmark = pytest.mark.kratos
EXAMPLE = Path(__file__).parents[1] / 'src/kratos_mcp/examples/cantilever'
RESPONSES = [{'name': 'tip', 'variable': 'DISPLACEMENT', 'point': [1., 0., 0.], 'component': 1}]
SPEC = {'kind': 'parameter', 'axes': [{'file': 'Materials.json',
        'pointer': '/properties/0/Material/Variables/YOUNG_MODULUS', 'values': [1.05e11, 2.1e11, 4.2e11]}]}


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv('KRATOS_MCP_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('OMP_NUM_THREADS', '1')
    monkeypatch.setenv('MKL_NUM_THREADS', '1')
    env = kratos_env.resolve()
    capability = subprocess.run([env.python, '-c',
        'import KratosMultiphysics.StructuralMechanicsApplication; import KratosMultiphysics.LinearSolversApplication'],
        env=env.build_env(), capture_output=True, text=True, timeout=60)
    if capability.returncode:
        pytest.skip('Study acceptance requires StructuralMechanicsApplication and LinearSolversApplication: ' + capability.stderr[-500:])
    destination = tmp_path / 'case'
    shutil.copytree(EXAMPLE, destination)
    yield destination
    for meta in studies.list_studies():
        if meta['state'] not in studies.TERMINAL_STATES:
            studies.cancel(meta['study_id'])


def wait(study_id, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = studies.results(study_id)
        if result['state'] in studies.TERMINAL_STATES or result['state'] == 'interrupted':
            return result
        time.sleep(.1)
    studies.cancel(study_id)
    pytest.fail('Study did not finish')


def test_three_value_stiffness_sweep(case):
    original = studies.inventory(case)
    meta = studies.start(str(case), SPEC, RESPONSES, max_concurrency=2)
    result = wait(meta['study_id'])
    assert result['state'] == 'succeeded', result
    values = [row['responses']['tip']['value'] for row in result['variants']]
    assert all(value < 0 for value in values)
    assert values[0] / values[1] == pytest.approx(2., rel=1e-5)
    assert values[1] / values[2] == pytest.approx(2., rel=1e-5)
    assert len({row['job']['case_dir'] for row in result['variants']}) == 3
    assert studies.inventory(case) == original


def test_three_mesh_resolutions(case):
    spec = {'kind': 'mesh', 'mesh': {'kind': 'rectangle', 'size': [1., .2]},
            'divisions': [[4, 1], [8, 2], [16, 4]]}
    meta = studies.start(str(case), spec, RESPONSES, max_concurrency=2)
    result = wait(meta['study_id'])
    assert result['state'] == 'succeeded', result
    assert [row['mesh']['num_nodes'] for row in result['variants']] == [10, 27, 85]
    values = [row['responses']['tip']['value'] for row in result['variants']]
    assert all(math.isfinite(value) and value < 0 for value in values)
    assert abs(values[0] - values[2]) > abs(values[1] - values[2]) > 0
    assert result['variants'][1]['comparisons']['tip']['finest']['absolute_difference'] < result['variants'][0]['comparisons']['tip']['finest']['absolute_difference']


def test_failed_child_preserves_numerical_results(case):
    spec = {'kind': 'parameter', 'axes': [{'file': 'ProjectParameters.json',
        'pointer': '/solver_settings/linear_solver_settings/solver_type',
        'values': ['LinearSolversApplication.sparse_lu', 'nonexistent_study_solver', 'LinearSolversApplication.sparse_lu']}]}
    meta = studies.start(str(case), spec, RESPONSES, max_concurrency=2)
    result = wait(meta['study_id'])
    assert result['state'] == 'completed_with_errors', result
    assert [row['state'] for row in result['variants']] == ['succeeded', 'failed', 'succeeded']
    assert result['variants'][0]['responses']['tip']['value'] == pytest.approx(result['variants'][2]['responses']['tip']['value'])


def test_resume_adopts_active_child_without_duplicate(case, monkeypatch):
    launch = studies._launch
    monkeypatch.setattr(studies, '_launch', lambda path: None)
    meta = studies.start(str(case), SPEC, RESPONSES)
    executions = case.parent / 'executions'
    for index, row in enumerate(meta['variants']):
        path = jobs._job_dir(row['job_id']) / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['command'] = [sys.executable, '-c',
            f"from pathlib import Path; import time; Path({str(executions)!r}).open('a').write('{index}\\n'); time.sleep(1)"]
        path.write_text(json.dumps(manifest))
    monkeypatch.setattr(studies, '_launch', launch)
    launch(studies.directory(meta['study_id']))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not executions.exists():
        time.sleep(.02)
    assert executions.exists()
    before = studies.status(meta['study_id'])
    active_pid = before['variants'][0]['job']['pid']
    os.kill(before['coordinator_pid'], signal.SIGKILL)
    studies._coordinators[meta['study_id']].wait(timeout=10)
    assert studies.status(meta['study_id'])['state'] == 'interrupted'
    studies.resume(meta['study_id'])
    result = wait(meta['study_id'])
    assert result['state'] == 'completed_with_errors'  # Dummy jobs have no VTK.
    assert executions.read_text().splitlines() == ['0', '1', '2']
    assert result['variants'][0]['job']['pid'] == active_pid


def test_queue_continues_after_submitting_process_exits(case):
    script = ('import json; from kratos_mcp import studies; '
              f"meta=studies.start({str(case)!r}, {SPEC!r}, {RESPONSES!r}); "
              "print(json.dumps({'study_id':meta['study_id']}))")
    submitted = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
    assert submitted.returncode == 0, submitted.stderr
    study_id = json.loads(submitted.stdout)['study_id']
    result = wait(study_id)
    assert result['state'] == 'succeeded', result
    assert len(result['variants']) == 3
