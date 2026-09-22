"""Streaming, physical-time-aware meshio result analysis."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import meshio
import numpy as np


SeriesSource = str | list[dict[str, Any]]


def records(source: SeriesSource, series: str | None = None) -> list[dict[str, Any]]:
    root = Path.cwd()
    if isinstance(source, str):
        path = Path(source).expanduser().resolve()
        root = path.parent
        source = json.loads(path.read_text())['records']
    if not isinstance(source, list) or not source:
        raise ValueError('Provide a nonempty result index or list of file/time records')
    if any(not isinstance(r, dict) or 'file' not in r or 'time' not in r for r in source):
        raise ValueError('Every result record requires file and physical time')
    names = {r.get('series', 'default') for r in source}
    if series is None and len(names) > 1:
        raise ValueError(f'Multiple series; select one of {sorted(names)}')
    chosen = series if series is not None else next(iter(names))
    result = []
    for row in source:
        if row.get('series', 'default') != chosen:
            continue
        time = float(row['time'])
        if not math.isfinite(time):
            raise ValueError('Physical times must be finite')
        path = (root / Path(row['file']).expanduser()).resolve()
        if not path.is_file():
            raise ValueError(f'Result file missing: {path}')
        result.append({**row, 'time': time, 'file': str(path), 'series': chosen})
    result.sort(key=lambda r: r['time'])
    if not result or len({r['time'] for r in result}) != len(result):
        raise ValueError('Series is empty or contains duplicate physical times')
    return result


def field(mesh: meshio.Mesh, variable: str, association: str) -> np.ndarray:
    if association not in ('point', 'cell'):
        raise ValueError('association must be point or cell')
    data = mesh.point_data if association == 'point' else mesh.cell_data
    if variable not in data:
        raise ValueError(f'Missing {association} field {variable}')
    value = data[variable] if association == 'point' else np.concatenate(data[variable], axis=0)
    value = np.asarray(value, dtype=float)
    count = len(mesh.points) if association == 'point' else sum(len(c.data) for c in mesh.cells)
    if value.ndim not in (1, 2) or len(value) != count or value.size == 0 or not np.isfinite(value).all():
        raise ValueError('Field must have finite, nonempty scalar/vector values for every entity')
    return value


def probe(mesh: meshio.Mesh, values: np.ndarray, association: str,
          point: list[float] | None, entity_index: int | None) -> dict[str, Any]:
    if (point is None) == (entity_index is None):
        raise ValueError('Provide exactly one of point or entity_index')
    positions = np.asarray(mesh.points) if association == 'point' else np.concatenate([
        np.asarray(mesh.points)[c.data].mean(axis=1) for c in mesh.cells])
    distance = 0.0
    if point is not None:
        target = np.asarray(point, dtype=float)
        if target.ndim != 1 or len(target) not in (2, 3) or not np.isfinite(target).all():
            raise ValueError('point must contain two or three finite coordinates')
        target = np.pad(target, (0, positions.shape[1] - len(target)))
        distances = np.linalg.norm(positions - target, axis=1)
        entity_index = int(distances.argmin())
        distance = float(distances[entity_index])
    if isinstance(entity_index, bool) or not isinstance(entity_index, int) or not 0 <= entity_index < len(values):
        raise ValueError('entity_index out of range or not an integer')
    return {'entity_index': entity_index, 'coordinates': positions[entity_index].tolist(),
            'distance_from_query': distance, 'value': values[entity_index].tolist()}


def export_csv(path: str, rows: list[dict[str, Any]]) -> str:
    destination = Path(path).expanduser().resolve()
    flattened = []
    for row in rows:
        flat = {}
        for key, value in row.items():
            if isinstance(value, list):
                flat.update({f'{key}_{i}': v for i, v in enumerate(value)})
            else:
                flat[key] = value
        flattened.append(flat)
    keys = list(dict.fromkeys(k for row in flattened for k in row))
    with destination.open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(flattened)
    return str(destination)


def history(source: SeriesSource, variable: str, association: str = 'point',
            point: list[float] | None = None, entity_index: int | None = None,
            series: str | None = None, csv_file: str | None = None) -> dict[str, Any]:
    rows = []
    value_shape = None
    for row in records(source, series):
        mesh = meshio.read(row['file'])
        sample = probe(mesh, field(mesh, variable, association), association, point, entity_index)
        shape = np.shape(sample['value'])
        if value_shape is not None and shape != value_shape:
            raise ValueError('Field component shape changed across the time series')
        value_shape = shape
        rows.append({**row, 'variable': variable, 'association': association, **sample})
    result = {'variable': variable, 'association': association, 'samples': rows}
    if csv_file:
        result['csv_file'] = export_csv(csv_file, rows)
    return result


def matching_mesh(reference: meshio.Mesh, candidate: meshio.Mesh) -> None:
    if not np.array_equal(reference.points, candidate.points) or len(reference.cells) != len(candidate.cells):
        raise ValueError('Mesh mismatch: point coordinates/order or cell blocks differ')
    for a, b in zip(reference.cells, candidate.cells):
        if a.type != b.type or not np.array_equal(a.data, b.data):
            raise ValueError('Mesh mismatch: cell types or connectivity differ')


def compare(reference: SeriesSource, candidate: SeriesSource, variable: str,
            mode: str = 'field', association: str = 'point',
            reference_series: str | None = None, candidate_series: str | None = None,
            point: list[float] | None = None, entity_index: int | None = None,
            atol: float = 1e-8, rtol: float = 1e-5, time_atol: float = 1e-12,
            csv_file: str | None = None) -> dict[str, Any]:
    if mode not in ('history', 'field'):
        raise ValueError('mode must be history or field')
    if any(not math.isfinite(x) or x < 0 for x in (atol, rtol, time_atol)):
        raise ValueError('Tolerances must be finite and nonnegative')
    refs, candidates = records(reference, reference_series), records(candidate, candidate_series)
    pairs, used = [], set()
    for ref in refs:
        matches = [i for i, item in enumerate(candidates) if abs(item['time'] - ref['time']) <= time_atol]
        if len(matches) != 1 or matches[0] in used:
            raise ValueError(f'Missing or ambiguous matching time for {ref["time"]}')
        used.add(matches[0])
        pairs.append((ref, candidates[matches[0]]))
    if len(used) != len(candidates):
        raise ValueError('Candidate contains unmatched physical times')
    rows, worst, total, failures, rms = [], None, 0, 0, 0.0
    for ref, candidate_row in pairs:
        a_mesh, b_mesh = meshio.read(ref['file']), meshio.read(candidate_row['file'])
        a, b = field(a_mesh, variable, association), field(b_mesh, variable, association)
        selected_index = None
        if mode == 'field':
            matching_mesh(a_mesh, b_mesh)
        else:
            pa = probe(a_mesh, a, association, point, entity_index)
            pb = probe(b_mesh, b, association, point, entity_index)
            selected_index = pa['entity_index']
            a, b = np.asarray(pa['value']), np.asarray(pb['value'])
        if a.shape != b.shape:
            raise ValueError('Field value shapes differ')
        diff = np.abs(b - a)
        if not np.isfinite(diff).all():
            raise ValueError('Difference overflowed; values cannot be compared')
        failed = int(np.count_nonzero(diff > atol + rtol * np.abs(a)))
        location = np.unravel_index(int(diff.argmax()), diff.shape) if diff.shape else ()
        maximum = float(diff.max())
        entity = selected_index if mode == 'history' else int(location[0])
        component = int(location[-1]) if (mode == 'history' and a.ndim == 1) or a.ndim == 2 else 0
        if worst is None or maximum > worst['absolute_error']:
            worst = dict(time=ref['time'], entity_index=entity, component=component, absolute_error=maximum)
        row_rms = maximum * float(np.sqrt(np.mean(np.square(diff / maximum)))) if maximum else 0.0
        rows.append(dict(time=ref['time'], candidate_time=candidate_row['time'], variable=variable,
                         association=association, reference_file=ref['file'], candidate_file=candidate_row['file'],
                         passed=failed == 0, max_absolute_error=maximum,
                         rms_error=row_rms, failure_count=failed,
                         component_max_absolute_error=np.atleast_1d(diff.max(axis=0) if mode == 'field' else diff).tolist()))
        if mode == 'history':
            rows[-1].update(reference_value=pa['value'], candidate_value=pb['value'],
                            reference_entity_index=pa['entity_index'], candidate_entity_index=pb['entity_index'],
                            reference_coordinates=pa['coordinates'], candidate_coordinates=pb['coordinates'])
        rms = math.hypot(rms * math.sqrt(total / (total + diff.size)),
                         row_rms * math.sqrt(diff.size / (total + diff.size)))
        total += diff.size
        failures += failed
    result = dict(passed=failures == 0, failure_count=failures, compared_components=total,
                  max_absolute_error=worst['absolute_error'], rms_error=rms,
                  worst=worst, comparisons=rows, atol=atol, rtol=rtol, time_atol=time_atol)
    if csv_file:
        result['csv_file'] = export_csv(csv_file, rows)
    return result
