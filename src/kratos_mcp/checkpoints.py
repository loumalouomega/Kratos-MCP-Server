"""Checkpoint configuration and verified, isolated restart preparation."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import tempfile
from typing import Any

from . import jobs, kratos_env


def serial_single(params: dict[str, Any]) -> None:
    if not isinstance(params, dict) or not isinstance(params.get('problem_data', {}), dict):
        raise ValueError('Project parameters and problem_data must be JSON objects')
    if 'stages' in params or 'orchestrator' in params:
        raise ValueError('Multistage checkpoints are not supported')
    if params.get('problem_data', {}).get('parallel_type', 'OpenMP') != 'OpenMP':
        raise ValueError('MPI checkpoints are not supported')


def restart_file(settings: dict[str, Any], case: str | Path) -> Path:
    base = Path(settings['input_filename'])
    if not settings.get('restart_load_file_label'):
        raise ValueError('restart_load_file_label must be specified')
    folder = settings.get('input_output_path') or base.name + '__restart_files'
    parent = base.parent / folder if settings.get('load_restart_files_from_folder', True) else base.parent
    return Path(case) / parent / (base.name + '_' + settings['restart_load_file_label'] + '.rest')


def configure(case_dir: str, frequency: float, control_type: str = 'time',
              max_files_to_keep: int = -1,
              parameters_file: str = 'ProjectParameters.json') -> dict[str, Any]:
    case = Path(case_dir).expanduser().resolve()
    jobs._relative_destination(parameters_file, key='parameters_file')
    path = case / parameters_file
    jobs._assert_inside(path, case, key='parameters_file')
    if not math.isfinite(frequency) or frequency <= 0:
        raise ValueError('frequency must be finite and positive')
    if control_type not in ('time', 'step') or (control_type == 'step' and int(frequency) != frequency):
        raise ValueError('control_type must be time or step; step frequency must be integral')
    if isinstance(max_files_to_keep, bool) or not isinstance(max_files_to_keep, int) or max_files_to_keep == 0 or max_files_to_keep < -1:
        raise ValueError('max_files_to_keep must be -1 or a positive integer')
    params = json.loads(path.read_text())
    serial_single(params)
    model = params['solver_settings']['model_part_name']
    if not model or '/' in model or '\\' in model or model in ('.', '..'):
        raise ValueError('Invalid model part name for checkpoint files')
    settings = dict(model_part_name=model, restart_save_frequency=frequency,
                    restart_control_type=control_type, max_files_to_keep=max_files_to_keep,
                    save_restart_files_in_folder=True, output_path='checkpoints')
    groups = params.setdefault('output_processes', {})
    matches = [p for group in groups.values() for p in group
               if p.get('python_module') == 'save_restart_process'
               and p.get('Parameters', {}).get('model_part_name') == model]
    if len(matches) > 1:
        raise ValueError('Multiple checkpoint processes target this model part')
    process = matches[0] if matches else {}
    settings['output_path'] = process.get('Parameters', {}).get('output_path') or 'checkpoints'
    process.update(python_module='save_restart_process', kratos_module='KratosMultiphysics',
                   process_name='SaveRestartProcess', Parameters=settings)
    if not matches:
        groups.setdefault('restart_output', []).append(process)
    jobs._validate_output_paths(params)
    jobs._assert_inside(case / settings['output_path'], case, key='checkpoint output')
    path.write_text(json.dumps(params, indent=2) + '\n')
    return {'parameters_file': str(path), 'settings': settings}


def list_checkpoints(job_id: str) -> dict[str, Any]:
    meta = jobs.refresh(job_id)
    root = Path(meta.case_dir)
    index = root / 'checkpoint-index.json'
    records = json.loads(index.read_text())['records'] if index.is_file() else []
    result = []
    for record in records:
        path = root / jobs._relative_destination(record['file'], key='checkpoint')
        jobs._assert_inside(path, root, key='checkpoint')
        result.append({**record, 'path': str(path), 'available': path.is_file()})
    return {'job_id': job_id, 'checkpoints': sorted(result, key=lambda r: (float(r['label']), r['file']))}


def verified_snapshot(job_id: str) -> tuple[jobs.JobMeta, Path, dict[str, Any]]:
    meta = jobs.refresh(job_id)
    directory = jobs._job_dir(job_id)
    snapshot = directory / 'snapshot'
    if (not meta.extra.get('snapshot_dir') or not snapshot.is_dir() or snapshot.is_symlink()
            or Path(meta.extra['snapshot_dir']).resolve() != snapshot.resolve()):
        raise ValueError('Resume requires an isolated job with a preserved snapshot')
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest['kratos_fingerprint'] != kratos_env.resolve().fingerprint():
        raise ValueError('Kratos build fingerprint changed')
    actual = {}
    for path in snapshot.rglob('*'):
        if path.is_symlink():
            raise ValueError('Snapshot contains a symlink')
        if path.is_file():
            actual[str(path.relative_to(snapshot))] = jobs._sha256(path)
    if actual != manifest['input_hashes']:
        raise ValueError('Snapshot input inventory or hashes changed')
    return meta, snapshot, manifest


def resume(job_id: str, checkpoint: str, end_time: float | None = None) -> jobs.JobMeta:
    meta, snapshot, manifest = verified_snapshot(job_id)
    if meta.state not in jobs.TERMINAL_STATES:
        raise ValueError('Resume requires a terminal source job; cancel it first')
    jobs._relative_destination(meta.parameters_file, key='parameters_file')
    params = json.loads((snapshot / meta.parameters_file).read_text())
    serial_single(params)
    records = list_checkpoints(job_id)['checkpoints']
    matches = [r for r in records if checkpoint in (r['file'], r['path'])]
    if len(matches) != 1 or not matches[0]['available']:
        raise ValueError('Checkpoint missing or incomplete; select an available path from job_checkpoints')
    record = matches[0]
    if record.get('model_part', params['solver_settings']['model_part_name']) != params['solver_settings']['model_part_name']:
        raise ValueError('Checkpoint model part does not match the solver model part')
    source = Path(record['path'])
    if jobs._sha256(source) != record['sha256']:
        raise ValueError('Checkpoint checksum mismatch')
    end = params['problem_data']['end_time'] if end_time is None else end_time
    if not math.isfinite(end) or end <= record['time']:
        raise ValueError('end_time must be finite and later than checkpoint time')
    params['problem_data']['end_time'] = end
    model = params['solver_settings']['model_part_name']
    with tempfile.TemporaryDirectory(prefix='kratos-resume-') as tmp:
        staged = Path(tmp) / 'case'
        shutil.copytree(snapshot, staged)
        copied_hashes = {str(p.relative_to(staged)): jobs._sha256(p)
                         for p in staged.rglob('*') if p.is_file()}
        if copied_hashes != manifest['input_hashes']:
            raise ValueError('Snapshot changed while copying')
        target = staged / 'restart_input' / (model + '_' + record['label'] + '.rest')
        jobs._assert_inside(target, staged, key='restart input')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if jobs._sha256(target) != record['sha256']:
            raise ValueError('Checkpoint changed while copying')
        params['solver_settings']['model_import_settings'] = {
            'input_type': 'rest', 'input_filename': 'restart_input/' + model,
            'restart_load_file_label': record['label'], 'load_restart_files_from_folder': False}
        (staged / meta.parameters_file).write_text(json.dumps(params, indent=2) + '\n')
        new = jobs.start(str(staged), meta.parameters_file, meta.analysis_type,
                         meta.extra.get('analysis_class'), isolate=True)
    lineage = {'resume_of': job_id, 'checkpoint': record,
               'original_case_dir': manifest['original_case_dir'],
               'external_inputs': manifest.get('external_inputs', {})}
    new.extra.update(lineage)
    directory = jobs._job_dir(new.job_id)
    new_manifest = json.loads((directory / 'manifest.json').read_text())
    new_manifest.update(lineage)
    (directory / 'manifest.json').write_text(json.dumps(new_manifest, indent=2) + '\n')
    jobs._write_meta(directory, new)
    return new
