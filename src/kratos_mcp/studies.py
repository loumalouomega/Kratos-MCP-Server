"""Persistent parameter and structured-mesh studies over isolated serial jobs."""
from __future__ import annotations

import copy
import itertools
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any

import numpy as np

from . import jobs, kratos_env, mdpa, result_series
from .job_supervisor import exclusive

TERMINAL_STATES = {'succeeded', 'completed_with_errors', 'cancelled'}


def root() -> Path:
    path = kratos_env.data_dir() / 'studies'
    path.mkdir(parents=True, exist_ok=True)
    return path


def directory(study_id: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9_-]+', study_id):
        raise ValueError('Invalid study ID')
    path = root() / study_id
    if not (path / 'meta.json').is_file():
        raise KeyError(f'Unknown study {study_id!r}')
    return path


def read(path: Path) -> dict[str, Any]:
    return json.loads((path / 'meta.json').read_text())


def write(path: Path, data: dict[str, Any]) -> None:
    temporary = path / f'meta.{uuid.uuid4().hex}.tmp'
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
    temporary.replace(path / 'meta.json')


def inventory(path: Path) -> dict[str, str]:
    result = {}
    for entry in path.rglob('*'):
        if entry.is_symlink():
            raise ValueError(f'Snapshot symlink is not allowed: {entry}')
        if entry.is_file():
            result[entry.relative_to(path).as_posix()] = jobs._sha256(entry)
    return result


def relative(value: str) -> str:
    if not isinstance(value, str) or not value or value == '.':
        raise ValueError('A nonempty case-relative filename is required')
    return jobs._relative_destination(value, key='study file')


def pointer_tokens(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('JSON Pointer must start with / and target an existing value')
    if re.search(r'~(?![01])', pointer):
        raise ValueError('Invalid JSON Pointer escape')
    return [part.replace('~1', '/').replace('~0', '~') for part in pointer[1:].split('/')]


def replace(document: Any, pointer: str, value: Any) -> None:
    tokens = pointer_tokens(pointer)
    parent = document
    for index, token in enumerate(tokens):
        if isinstance(parent, list):
            if not re.fullmatch(r'0|[1-9][0-9]*', token) or int(token) >= len(parent):
                raise ValueError(f'JSON Pointer array index does not exist: {pointer}')
            key: Any = int(token)
        elif isinstance(parent, dict) and token in parent:
            key = token
        else:
            raise ValueError(f'JSON Pointer target does not exist: {pointer}')
        if index == len(tokens) - 1:
            parent[key] = copy.deepcopy(value)
        else:
            parent = parent[key]


def expand(spec: dict[str, Any]) -> list[list[dict[str, Any]]]:
    axes = spec.get('axes')
    if not isinstance(axes, list) or not axes:
        raise ValueError('Parameter studies require nonempty axes')
    targets = []
    normalized = []
    for axis in axes:
        if not isinstance(axis, dict) or set(axis) != {'file', 'pointer', 'values'}:
            raise ValueError('Each axis requires exactly file, pointer, and values')
        filename = relative(axis['file'])
        tokens = pointer_tokens(axis['pointer'])
        for old_file, old_tokens in targets:
            if filename == old_file and (tokens[:len(old_tokens)] == old_tokens or old_tokens[:len(tokens)] == tokens):
                raise ValueError('Duplicate or overlapping JSON Pointer targets')
        targets.append((filename, tokens))
        if not isinstance(axis['values'], list) or not axis['values']:
            raise ValueError('Axis values must be a nonempty list')
        json.dumps(axis['values'], allow_nan=False)
        normalized.append({**axis, 'file': filename})
    combination = spec.get('combination', 'product')
    values = [axis['values'] for axis in normalized]
    if combination == 'zip':
        if len({len(items) for items in values}) != 1:
            raise ValueError('Zip axes must have equal lengths')
        rows = zip(*values)
    elif combination == 'product':
        rows = itertools.product(*values)
    else:
        raise ValueError('combination must be product or zip')
    return [[{'file': axis['file'], 'pointer': axis['pointer'], 'value': value}
             for axis, value in zip(normalized, row)] for row in rows]


def positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f'{name} must be a positive integer')
    return value


def finite(value: Any, name: str, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f'{name} must be finite and >= {minimum}')
    return float(value)


def validate_responses(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(responses, list) or not responses:
        raise ValueError('At least one named probe response is required')
    names = set()
    normalized = []
    allowed = {'name', 'variable', 'association', 'point', 'series', 'component', 'reduction', 'max_distance'}
    for item in responses:
        if not isinstance(item, dict) or set(item) - allowed:
            raise ValueError('Invalid response specification keys')
        item = dict(item)
        for key in ('name', 'variable'):
            if not isinstance(item.get(key), str) or not item[key]:
                raise ValueError(f'Response {key} must be a nonempty string')
        if item['name'] in names:
            raise ValueError('Response names must be unique')
        names.add(item['name'])
        point = item.get('point')
        if not isinstance(point, list) or len(point) not in (2, 3):
            raise ValueError('Response point must have two or three coordinates')
        for coordinate in point:
            finite(coordinate, 'point coordinate', -float('inf'))
        item.setdefault('association', 'point')
        item.setdefault('reduction', 'final')
        if item['association'] not in ('point', 'cell') or item['reduction'] not in ('final', 'min', 'max'):
            raise ValueError('Invalid response association or reduction')
        component = item.get('component')
        if component is not None and component != 'magnitude' and (
                isinstance(component, bool) or not isinstance(component, int) or component < 0):
            raise ValueError('component must be a nonnegative index or magnitude')
        if item.get('series') is not None and not isinstance(item['series'], str):
            raise ValueError('series must be a string')
        if item.get('max_distance') is not None:
            finite(item['max_distance'], 'max_distance')
        normalized.append(item)
    return normalized


def mesh_levels(spec: dict[str, Any]) -> list[list[int]]:
    mesh = spec.get('mesh', {})
    if not isinstance(mesh, dict) or set(mesh) - {'kind', 'size', 'element_name', 'condition_name', 'triangles'}:
        raise ValueError('Invalid structured mesh options')
    dimension = {'line': 1, 'rectangle': 2, 'box': 3}.get(mesh.get('kind'))
    if dimension is None:
        raise ValueError('mesh.kind must be line, rectangle, or box')
    size = mesh.get('size')
    if not isinstance(size, list) or len(size) != dimension:
        raise ValueError('mesh.size must match its dimension')
    for value in size:
        if finite(value, 'mesh size') == 0:
            raise ValueError('Mesh sizes must be positive')
    if not isinstance(mesh.get('triangles', False), bool) or (mesh.get('triangles') and dimension != 2):
        raise ValueError('triangles applies only to rectangle meshes')
    for key in ('element_name', 'condition_name'):
        if mesh.get(key) is not None and (not isinstance(mesh[key], str) or not mesh[key].strip()):
            raise ValueError(f'{key} must be a nonempty string')
    levels = spec.get('divisions')
    if not isinstance(levels, list) or len(levels) < 2:
        raise ValueError('Mesh studies require at least two division vectors')
    previous = None
    for level in levels:
        if not isinstance(level, list) or len(level) != dimension:
            raise ValueError('divisions must match the mesh dimension')
        for value in level:
            positive_int(value, 'division')
        if previous and (any(a < b for a, b in zip(level, previous)) or level == previous):
            raise ValueError('Mesh divisions must be progressively finer')
        previous = level
    return levels


def generate(options: dict[str, Any], divisions: list[int]) -> mdpa.Mdpa:
    size = options['size']
    kind = options['kind']
    element = options.get('element_name')
    condition = options.get('condition_name')
    if kind == 'line':
        return mdpa.create_line_mesh(size[0], divisions[0], element or 'TrussLinearElement2D2N', condition)
    if kind == 'rectangle':
        triangles = options.get('triangles', False)
        return mdpa.create_rectangle_mesh(*size, *divisions,
            element_name=element or ('SmallDisplacementElement2D3N' if triangles else 'SmallDisplacementElement2D4N'),
            condition_name=condition or 'LineLoadCondition2D2N', triangles=triangles)
    return mdpa.create_box_mesh(*size, *divisions, element_name=element or 'SmallDisplacementElement3D8N',
                               condition_name=condition or 'SurfaceLoadCondition3D4N')


def serial_case(params: dict[str, Any]) -> None:
    if not isinstance(params, dict) or 'stages' in params or 'orchestrator' in params:
        raise ValueError('Studies require serial single-stage cases')
    if any(not isinstance(params.get(key), dict) for key in ('problem_data', 'solver_settings')):
        raise ValueError('problem_data and solver_settings must be objects')
    if params.get('problem_data', {}).get('parallel_type', 'OpenMP') != 'OpenMP':
        raise ValueError('MPI studies are not supported')
    def check(value):
        if isinstance(value, dict):
            for key in ('input_filename', 'materials_filename'):
                if key in value and (not isinstance(value[key], str) or not value[key]):
                    raise ValueError(f'{key} must be a nonempty filename')
            if value.get('input_type') == 'rest':
                raise ValueError('Restart-input studies are not supported')
            solver = str(value.get('solver_type', '')).lower()
            if 'mpi' in solver or 'trilinos' in solver:
                raise ValueError('MPI solver settings are not supported')
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
    check(params)


def start(case_dir: str, specification: dict[str, Any], responses: list[dict[str, Any]],
          max_concurrency: int = 1, parameters_file: str = 'ProjectParameters.json',
          analysis_type: str | None = None, analysis_class: str | None = None,
          external_inputs: dict[str, str] | None = None) -> dict[str, Any]:
    from .tools.scaffold import validate_case_files
    if os.name != 'posix':
        raise ValueError('Detached studies currently require POSIX process groups and file locks')
    positive_int(max_concurrency, 'max_concurrency')
    parameters_file = relative(parameters_file)
    responses = validate_responses(responses)
    if not isinstance(specification, dict):
        raise ValueError('specification must be an object')
    kind = specification.get('kind')
    allowed = {'kind', 'atol', 'rtol', 'time_atol'} | (
        {'axes', 'combination'} if kind == 'parameter' else {'mesh', 'divisions'})
    if kind not in ('parameter', 'mesh') or set(specification) - allowed:
        raise ValueError('Use a parameter or mesh study with the documented specification keys')
    tolerances = {key: finite(specification.get(key, default), key)
                  for key, default in (('atol', 1e-8), ('rtol', 1e-5), ('time_atol', 1e-12))}
    variants = expand(specification) if kind == 'parameter' else mesh_levels(specification)
    case = Path(case_dir).expanduser().resolve()
    serial_case(json.loads((case / parameters_file).read_text()))
    env = kratos_env.resolve()
    if not kratos_env.is_available(env):
        raise RuntimeError('Kratos is not available; cannot start a study')
    study_id = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12]
    path = root() / study_id
    path.mkdir()
    prepared = []
    try:
        base = path / 'base'
        base.mkdir()
        snapshot, execution, hashes = jobs._prepare_isolated_case(case, parameters_file, base, external_inputs)
        shutil.rmtree(execution)
        meta = {'version': 1, 'study_id': study_id, 'state': 'preparing', 'created_at': time.time(),
                'finished_at': None, 'case_dir': str(case), 'parameters_file': parameters_file,
                'specification': specification, 'responses': responses, 'tolerances': tolerances,
                'max_concurrency': max_concurrency, 'base_hashes': hashes,
                'kratos_fingerprint': env.fingerprint(), 'external_inputs': external_inputs or {},
                'analysis_type': analysis_type, 'analysis_class': analysis_class, 'variants': []}
        # All variants are statically checked before preparing any launchable jobs.
        for index, variant in enumerate(variants):
            inputs = path / 'inputs' / str(index)
            shutil.copytree(snapshot, inputs)
            row = {'index': index, 'job_id': f'{study_id}-{index:04d}', 'state': 'queued',
                   'responses': {}, 'overrides': variant if kind == 'parameter' else []}
            if kind == 'parameter':
                for override in variant:
                    target = inputs / override['file']
                    document = json.loads(target.read_text())
                    replace(document, override['pointer'], override['value'])
                    target.write_text(json.dumps(document, indent=2, allow_nan=False) + '\n')
            params = json.loads((inputs / parameters_file).read_text())
            serial_case(params)
            if kind == 'mesh':
                model_import = params['solver_settings']['model_import_settings']
                if model_import.get('input_type', 'mdpa') != 'mdpa':
                    raise ValueError('Mesh studies require a single MDPA model import')
                filename = relative(model_import['input_filename'])
                target = inputs / (filename if filename.endswith('.mdpa') else filename + '.mdpa')
                mesh = generate(specification['mesh'], variant)
                mesh.write(target)
                row.update(divisions=variant, mesh=mesh.inspect())
            jobs._validate_output_paths(params)
            jobs._rewrite_references(params, inputs.resolve(), {}, {})
            validation = validate_case_files(inputs, parameters_file, deep=False)
            if not validation['valid']:
                raise ValueError(f'Variant {index}: ' + '; '.join(validation['issues']))
            row['validation'] = validation
            meta['variants'].append(row)
        for row in meta['variants']:
            child = jobs.start(str(path / 'inputs' / str(row['index'])), parameters_file,
                               analysis_type, analysis_class, isolate=True,
                               _job_id=row['job_id'], _defer_launch=True)
            prepared.append(child.job_id)
            child.extra.update(study_id=study_id, variant_index=row['index'], original_case_dir=str(case))
            jobs._write_meta(jobs._job_dir(child.job_id), child)
            manifest_path = jobs._job_dir(child.job_id) / 'manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest.update(original_case_dir=str(case), study_id=study_id,
                            variant_index=row['index'], overrides=row['overrides'],
                            mesh=row.get('mesh'), divisions=row.get('divisions'))
            manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
            row['input_hashes'] = inventory(Path(child.extra['snapshot_dir']))
        shutil.rmtree(path / 'inputs')
        meta['state'] = 'interrupted'
        write(path, meta)
    except Exception:
        for job_id in prepared:
            shutil.rmtree(jobs._job_dir(job_id))
        shutil.rmtree(path)
        raise
    _launch(path)
    return status(study_id)


_coordinators: dict[str, subprocess.Popen] = {}


def _launch(path: Path) -> None:
    # Parent publishes metadata before allowing the coordinator to acquire ownership.
    with exclusive(path / 'owner.lock'):
        meta = read(path)
        if meta['state'] != 'interrupted':
            raise ValueError('Only interrupted studies can be resumed')
        run_env = os.environ.copy()
        run_env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent) + os.pathsep + run_env.get('PYTHONPATH', '')
        with (path / 'coordinator.log').open('ab') as log:
            proc = subprocess.Popen([sys.executable, '-u', '-m', 'kratos_mcp.study_coordinator', meta['study_id']],
                                    env=run_env, stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        meta.update(state='running', coordinator_pid=proc.pid, coordinator_started=False)
        meta.pop('coordinator_error', None)
        write(path, meta)
        _coordinators[meta['study_id']] = proc


def status(study_id: str) -> dict[str, Any]:
    path = directory(study_id)
    proc = _coordinators.get(study_id)
    if proc is not None and proc.poll() is not None:
        _coordinators.pop(study_id, None)
    meta = read(path)
    if meta['state'] == 'running':
        try:
            with exclusive(path / 'owner.lock'):
                meta = read(path)
                if meta['state'] == 'running' and (meta.get('coordinator_started') or not jobs._pid_alive(meta['coordinator_pid'])):
                    meta['state'] = 'interrupted'
                    write(path, meta)
        except BlockingIOError:
            pass
    result = copy.deepcopy(meta)
    for row in result['variants']:
        row['job'] = jobs.status(row['job_id'])
    result['study_dir'] = str(path)
    return result


def list_studies(state: str | None = None) -> list[dict[str, Any]]:
    return [item for path in sorted(root().iterdir()) if (path / 'meta.json').is_file()
            for item in [status(path.name)] if state is None or item['state'] == state]


def verify(meta: dict[str, Any], path: Path) -> None:
    if kratos_env.resolve().fingerprint() != meta['kratos_fingerprint']:
        raise RuntimeError('Kratos build fingerprint changed; refusing to resume study')
    if inventory(path / 'base' / 'snapshot') != meta['base_hashes']:
        raise RuntimeError('Base snapshot changed; refusing to resume study')
    for row in meta['variants']:
        child = jobs._job_dir(row['job_id'])
        if inventory(child / 'snapshot') != row['input_hashes']:
            raise RuntimeError(f'Variant {row["index"]} snapshot changed')
        job = jobs.status(row['job_id'])
        if job['state'] == 'queued' and inventory(child / 'execution') != row['input_hashes']:
            raise RuntimeError(f'Variant {row["index"]} execution inputs changed')


def resume(study_id: str) -> dict[str, Any]:
    meta = status(study_id)
    if meta['state'] != 'interrupted':
        raise ValueError('Only interrupted studies can be resumed')
    path = directory(study_id)
    verify(meta, path)
    _launch(path)
    return status(study_id)


def cancel(study_id: str) -> dict[str, Any]:
    path = directory(study_id)
    meta = status(study_id)
    if meta['state'] in TERMINAL_STATES:
        return meta
    (path / 'cancel.request').touch()
    # Every identity already exists, so cancelling queued children also closes
    # the race with a coordinator that has just decided to launch one.
    for row in meta['variants']:
        jobs.cancel(row['job_id'])
    try:
        with exclusive(path / 'owner.lock'):
            meta = read(path)
            for row in meta['variants']:
                collect(row, jobs.refresh(row['job_id']), meta['responses'])
            meta.update(state='cancelled', finished_at=time.time())
            write(path, meta)
    except BlockingIOError:
        pass
    return status(study_id)


def collect(row: dict[str, Any], child: jobs.JobMeta, responses: list[dict[str, Any]]) -> None:
    """Collect each terminal child once, retaining independent response failures."""
    row['state'] = child.state
    if child.state not in jobs.TERMINAL_STATES or row.get('collected'):
        return
    if child.state == 'succeeded':
        for response in responses:
            try:
                row['responses'][response['name']] = extract(child.job_id, response)
            except Exception as exc:
                row['responses'][response['name']] = {'error': str(exc)}
    else:
        row['error'] = child.extra.get('launch_error') or child.extra.get('supervisor_error') or child.state
        if (jobs._job_dir(child.job_id) / 'stdout.log').exists():
            row['log_tail'] = jobs.logs(child.job_id, tail=30)
    row['collected'] = True


def extract(job_id: str, response: dict[str, Any]) -> dict[str, Any]:
    child = jobs.status(job_id)
    history = result_series.history(str(Path(child['case_dir']) / 'result-index.json'),
                                    response['variable'], response['association'],
                                    point=response['point'], series=response.get('series'))
    values = []
    for sample in history['samples']:
        if response.get('max_distance') is not None and sample['distance_from_query'] > response['max_distance']:
            raise ValueError('Probe exceeds max_distance')
        value = np.asarray(sample['value'])
        component = response.get('component')
        if component == 'magnitude':
            scalar = float(np.linalg.norm(value)) if value.ndim else abs(float(value))
        elif isinstance(component, int):
            if value.ndim != 1 or component >= len(value):
                raise ValueError('Probe component is out of range')
            scalar = float(value[component])
        elif value.ndim == 0:
            scalar = float(value)
        else:
            raise ValueError('Vector responses require component or magnitude')
        if not math.isfinite(scalar):
            raise ValueError('Response is not finite')
        sample['response_value'] = scalar
        values.append(scalar)
    reduction = response['reduction']
    value = values[-1] if reduction == 'final' else min(values) if reduction == 'min' else max(values)
    return {'value': value, 'reduction': reduction, 'samples': history['samples']}


def comparison(candidate: dict, reference: dict, tolerances: dict) -> dict:
    if 'error' in candidate or 'error' in reference:
        return {'error': 'Response unavailable'}
    times = [row['time'] for row in candidate['samples']]
    refs = [row['time'] for row in reference['samples']]
    matched = []
    for reference_time in refs:
        indices = [i for i, value in enumerate(times) if abs(value - reference_time) <= tolerances['time_atol']]
        if len(indices) != 1 or indices[0] in matched:
            return {'error': 'Physical-time samples do not match uniquely'}
        matched.extend(indices)
    if len(matched) != len(times):
        return {'error': 'Physical-time samples do not match uniquely'}
    difference = abs(candidate['value'] - reference['value'])
    if not math.isfinite(difference):
        return {'error': 'Response difference overflowed'}
    relative_difference = difference / abs(reference['value']) if reference['value'] != 0 else None
    return {'absolute_difference': difference,
            'relative_difference': relative_difference if relative_difference is None or math.isfinite(relative_difference) else None,
            'within_tolerance': difference <= tolerances['atol'] + tolerances['rtol'] * abs(reference['value'])}


def results(study_id: str, csv_file: str | None = None) -> dict[str, Any]:
    meta = status(study_id)
    rows = copy.deepcopy(meta['variants'])
    if meta['specification']['kind'] == 'mesh':
        for index, row in enumerate(rows):
            row['comparisons'] = {}
            for response in meta['responses']:
                name = response['name']
                candidate = row['responses'].get(name, {'error': 'Response unavailable'})
                comparisons = {}
                for label, reference in [('previous', rows[index - 1] if index else None), ('finest', rows[-1])]:
                    comparisons[label] = (comparison(candidate, reference['responses'].get(name, {'error': 'Response unavailable'}), meta['tolerances'])
                                          if reference is not None else {'error': 'No previous resolution'})
                row['comparisons'][name] = comparisons
    result = {'study_id': study_id, 'state': meta['state'], 'variants': rows}
    if csv_file:
        flat = []
        for row in rows:
            for response in meta['responses']:
                name = response['name']
                response_result = row['responses'].get(name, {})
                item = {'variant_index': row['index'], 'job_id': row['job_id'], 'state': row['job']['state'],
                        'overrides': json.dumps(row['overrides']), 'divisions': json.dumps(row.get('divisions')),
                        'response': name, 'value': response_result.get('value'),
                        'error': response_result.get('error', row.get('error', ''))}
                for label, value in row.get('comparisons', {}).get(name, {}).items():
                    item.update({f'{label}_{key}': value for key, value in value.items()})
                flat.append(item)
        result['csv_file'] = result_series.export_csv(csv_file, flat)
    return result
