import json
from pathlib import Path

import pytest

from kratos_mcp import checkpoints, jobs


def case(tmp_path):
    (tmp_path / 'ProjectParameters.json').write_text(json.dumps({
        'problem_data': {'end_time': 1},
        'solver_settings': {'model_part_name': 'Structure'}}))
    return tmp_path


def test_configuration_idempotent(tmp_path):
    case(tmp_path)
    checkpoints.configure(str(tmp_path), 2, 'step')
    checkpoints.configure(str(tmp_path), .2)
    params = json.loads((tmp_path / 'ProjectParameters.json').read_text())
    processes = params['output_processes']['restart_output']
    assert len(processes) == 1
    assert processes[0]['Parameters']['restart_save_frequency'] == .2


@pytest.mark.parametrize('frequency,control,retention', [(0,'time',-1), (float('nan'),'time',-1),
    (1.5,'step',-1), (1,'bad',-1), (1,'time',0), (1,'time',-2)])
def test_configuration_invalid(tmp_path, frequency, control, retention):
    case(tmp_path)
    with pytest.raises(ValueError):
        checkpoints.configure(str(tmp_path), frequency, control, retention)


def test_listing_retention_and_incomplete(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(jobs, 'refresh', lambda _: SimpleNamespace(case_dir=str(tmp_path)))
    (tmp_path / 'a.rest').write_bytes(b'completed')
    (tmp_path / 'incomplete.rest').write_bytes(b'partial')
    (tmp_path / 'checkpoint-index.json').write_text(json.dumps({'records': [
        {'file': 'gone.rest', 'label': '10', 'time': 10, 'step': 10},
        {'file': 'a.rest', 'label': '2', 'time': 2, 'step': 2}]}))
    rows = checkpoints.list_checkpoints('job')['checkpoints']
    assert [r['label'] for r in rows] == ['2', '10']
    assert [r['available'] for r in rows] == [True, False]


def test_restart_reference(tmp_path):
    settings = {'input_type': 'rest', 'input_filename': 'restart_input/Structure',
                'restart_load_file_label': '2', 'load_restart_files_from_folder': False}
    file = checkpoints.restart_file(settings, tmp_path)
    file.parent.mkdir()
    file.write_bytes(b'restart')
    assert jobs._rewrite_references(settings, tmp_path, {}, {}) == settings
    file.unlink()
    with pytest.raises(ValueError, match='not found'):
        jobs._rewrite_references(settings, tmp_path, {}, {})


@pytest.mark.asyncio
async def test_tool_registration_and_errors(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "state"))
    from mcp.server.fastmcp import FastMCP
    from kratos_mcp.tools import simulation, postprocess
    mcp = FastMCP('test')
    simulation.register(mcp)
    postprocess.register(mcp)
    tools = {t.name for t in await mcp.list_tools()}
    assert {'configure_checkpoints', 'job_checkpoints', 'job_resume',
            'results_time_history', 'results_compare'} <= tools
    result = await asyncio.wait_for(mcp.call_tool('job_checkpoints', {'job_id': 'missing'}), timeout=5)
    assert 'error' in str(result)


@pytest.fixture
def preserved_job(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('KRATOS_MCP_HOME', str(tmp_path / 'state'))
    monkeypatch.setattr(checkpoints.kratos_env, 'resolve', lambda: SimpleNamespace(fingerprint=lambda: 'build'))
    directory = jobs.jobs_root() / 'parent'
    snapshot = directory / 'snapshot'
    snapshot.mkdir(parents=True)
    case(snapshot)
    execution = directory / 'execution'
    execution.mkdir()
    checkpoint = execution / 'Structure_2.rest'
    checkpoint.write_bytes(b'serialized model')
    record = {'file': checkpoint.name, 'time': .2, 'step': 2, 'label': '2',
              'sha256': jobs._sha256(checkpoint), 'size': checkpoint.stat().st_size}
    (execution / 'checkpoint-index.json').write_text(json.dumps({'records': [record]}))
    manifest = {'kratos_fingerprint': 'build', 'original_case_dir': str(tmp_path / 'original'),
                'input_hashes': {'ProjectParameters.json': jobs._sha256(snapshot / 'ProjectParameters.json')}}
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    meta = jobs.JobMeta('parent', str(execution), 'ProjectParameters.json', state='cancelled',
                        extra={'snapshot_dir': str(snapshot)})
    jobs._write_meta(directory, meta)
    return meta, snapshot, checkpoint


@pytest.mark.parametrize('change,match', [('input','hashes'), ('fingerprint','fingerprint'),
    ('checkpoint','checksum'), ('missing','missing'), ('active','terminal'), ('end','later')])
def test_resume_rejects_invalid_source(preserved_job, change, match, monkeypatch):
    meta, snapshot, checkpoint = preserved_job
    end = None
    if change == 'input':
        (snapshot / 'ProjectParameters.json').write_text('{}')
    elif change == 'fingerprint':
        from types import SimpleNamespace
        monkeypatch.setattr(checkpoints.kratos_env, 'resolve', lambda: SimpleNamespace(fingerprint=lambda: 'other'))
    elif change == 'checkpoint':
        checkpoint.write_bytes(b'corrupt')
    elif change == 'missing':
        checkpoint.unlink()
    elif change == 'active':
        meta.state = 'running'
        monkeypatch.setattr(jobs, 'refresh', lambda _: meta)
    elif change == 'end':
        end = .2
    with pytest.raises(ValueError, match=match):
        checkpoints.resume(meta.job_id, str(checkpoint), end)


def test_resume_copies_inputs_and_lineage(preserved_job, monkeypatch):
    meta, snapshot, checkpoint = preserved_job
    original = (snapshot / 'ProjectParameters.json').read_bytes()
    captured = {}
    def start(case_dir, parameters_file, analysis_type, analysis_class, isolate):
        directory = jobs.jobs_root() / 'child'
        directory.mkdir()
        new_snapshot, execution, hashes = jobs._prepare_isolated_case(
            Path(case_dir), parameters_file, directory, None)
        captured['params'] = json.loads((new_snapshot / parameters_file).read_text())
        captured['checkpoint'] = (new_snapshot / 'restart_input/Structure_2.rest').read_bytes()
        (directory / 'manifest.json').write_text('{}')
        return jobs.JobMeta('child', str(execution), parameters_file,
                            extra={'snapshot_dir': str(new_snapshot)})
    monkeypatch.setattr(jobs, 'start', start)
    child = checkpoints.resume('parent', str(checkpoint))
    assert child.extra['resume_of'] == 'parent'
    assert captured['checkpoint'] == checkpoint.read_bytes()
    assert captured['params']['solver_settings']['model_import_settings']['input_type'] == 'rest'
    assert (snapshot / 'ProjectParameters.json').read_bytes() == original
    assert Path(child.case_dir) != Path(meta.case_dir)


@pytest.mark.parametrize('params', [{'stages': {}}, {'problem_data': {'parallel_type': 'MPI'}}])
def test_unsupported_checkpoint_cases(params):
    with pytest.raises(ValueError, match='not supported'):
        checkpoints.serial_single(params)


def test_output_index_publishes_only_completed_writes(tmp_path, monkeypatch):
    import hashlib
    import sys
    from types import ModuleType, SimpleNamespace
    from kratos_mcp.runner import install_output_indexes

    monkeypatch.chdir(tmp_path)
    model = SimpleNamespace(ProcessInfo={'TIME': .2, 'STEP': 2}, FullName=lambda: 'Structure')
    class Save:
        fail = True
        def __init__(self, unused_model, unused_settings):
            self.restart_utility = SimpleNamespace(model_part=model, raw_path=str(tmp_path),
                input_output_path='checkpoints', save_restart_files_in_folder=True)
        def PrintOutput(self):
            folder = tmp_path / 'checkpoints'
            folder.mkdir(exist_ok=True)
            (folder / 'Structure_2.rest').write_bytes(b'partial' if self.fail else b'complete')
            if self.fail:
                raise RuntimeError('Interrupted serialization')
    class Vtk:
        def __init__(self):
            pass
        def PrintOutput(self):
            pass
    for name, cls in [('save_restart_process', Save), ('vtk_output_process', Vtk)]:
        module = ModuleType('KratosMultiphysics.' + name)
        setattr(module, 'SaveRestartProcess' if name.startswith('save') else 'VtkOutputProcess', cls)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    install_output_indexes(SimpleNamespace(TIME='TIME', STEP='STEP'))
    process = Save(None, None)
    with pytest.raises(RuntimeError, match='Interrupted'):
        process.PrintOutput()
    assert not (tmp_path / 'checkpoint-index.json').exists()
    process.fail = False
    process.PrintOutput()
    record = json.loads((tmp_path / 'checkpoint-index.json').read_text())['records'][0]
    assert record['sha256'] == hashlib.sha256(b'complete').hexdigest()
    assert (record['time'], record['step'], record['label']) == (.2, 2, '2')


def test_configuration_rejects_escaping_output_without_editing(tmp_path):
    case(tmp_path)
    checkpoints.configure(str(tmp_path), 1)
    path = tmp_path / 'ProjectParameters.json'
    params = json.loads(path.read_text())
    params['output_processes']['restart_output'][0]['Parameters']['output_path'] = '../outside'
    path.write_text(json.dumps(params))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='inside'):
        checkpoints.configure(str(tmp_path), 2)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_new_tool_error_payloads(tmp_path, monkeypatch):
    import asyncio
    from mcp.server.fastmcp import FastMCP
    from kratos_mcp.tools import simulation, postprocess
    monkeypatch.setenv('KRATOS_MCP_HOME', str(tmp_path / 'state'))
    mcp = FastMCP('errors')
    simulation.register(mcp)
    postprocess.register(mcp)
    calls = [
        ('configure_checkpoints', {'case_dir': str(tmp_path), 'frequency': 0}),
        ('job_resume', {'job_id': 'missing', 'checkpoint': '/missing.rest'}),
        ('results_time_history', {'source': [], 'variable': 'T', 'entity_index': 0}),
        ('results_compare', {'reference': [], 'candidate': [], 'variable': 'T'}),
    ]
    for name, arguments in calls:
        result = await asyncio.wait_for(mcp.call_tool(name, arguments), timeout=5)
        assert 'error' in str(result), (name, result)
