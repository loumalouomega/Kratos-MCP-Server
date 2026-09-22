import csv
import json

import meshio
import numpy as np
import pytest

from kratos_mcp import result_series as rs


def write(path, value=1., shift=0.):
    meshio.write(path, meshio.Mesh(
        [[shift, 0., 0.], [1., 0., 0.], [0., 1., 0.]], [('triangle', [[0, 1, 2]])],
        point_data={'T': np.full(3, value), 'V': np.full((3, 3), value)},
        cell_data={'T': [np.array([value])]}))
    return str(path)


def test_history_physical_time_csv_and_cells(tmp_path):
    late = write(tmp_path / '1.vtu', 9.)
    early = write(tmp_path / '20.vtu', 2.)
    records = [{'file': late, 'time': .8}, {'file': early, 'time': .15}]
    csv_file = tmp_path / 'history.csv'
    result = rs.history(records, 'V', point=[0, 0], csv_file=str(csv_file))
    assert [r['time'] for r in result['samples']] == [.15, .8]
    assert result['samples'][0]['value'] == [2., 2., 2.]
    with csv_file.open() as stream:
        assert list(csv.DictReader(stream))[0]['value_2'] == '2.0'
    with pytest.raises(FileExistsError):
        rs.history(records, 'T', entity_index=0, csv_file=str(csv_file))
    cell = rs.history(records, 'T', association='cell', point=[0, 0])['samples'][0]
    assert cell['coordinates'] == pytest.approx([1/3, 1/3, 0])
    assert cell['value'] == 2.


def test_index_series_and_invalid_times(tmp_path):
    file = write(tmp_path / 'a.vtu')
    rows = [{'file': 'a.vtu', 'time': 1, 'series': s} for s in ['a', 'b']]
    index = tmp_path / 'result-index.json'
    index.write_text(json.dumps({'records': rows}))
    with pytest.raises(ValueError, match='Multiple series'):
        rs.records(str(index))
    assert rs.records(str(index), 'a')[0]['file'] == file
    for times in ([1, 1], [float('nan')], [float('inf')]):
        with pytest.raises(ValueError):
            rs.records([{'file': file, 'time': t} for t in times])


def test_comparison_tolerances_and_mesh(tmp_path):
    a = [{'file': write(tmp_path / 'a.vtu', 0), 'time': 1}]
    b = [{'file': write(tmp_path / 'b.vtu', 1), 'time': 1}]
    assert rs.compare(a, b, 'V', atol=1, rtol=0)['passed']
    result = rs.compare(a, b, 'V', atol=.5, rtol=0)
    assert result['failure_count'] == 9
    assert result['rms_error'] == 1
    assert result['worst']['time'] == 1
    assert rs.compare(a, b, 'T', mode='history', entity_index=0)['failure_count'] == 1
    assert rs.compare(a, b, 'T', association='cell')['failure_count'] == 1
    write(tmp_path / 'b.vtu', shift=.1)
    with pytest.raises(ValueError, match='Mesh mismatch'):
        rs.compare(a, b, 'T')
    with pytest.raises(ValueError, match='Missing'):
        rs.compare(a, [{'file': b[0]['file'], 'time': 2}], 'T')
    with pytest.raises(ValueError, match='ambiguous'):
        rs.compare(a, [b[0], {**b[0], 'time': 1+1e-13}], 'T')
    with pytest.raises(ValueError, match='Missing point field'):
        rs.history(a, 'missing', entity_index=0)
    write(tmp_path / 'a.vtu', float('nan'))
    with pytest.raises(ValueError, match='finite'):
        rs.history(a, 'T', entity_index=0)


def test_comparison_csv_vectors_and_connectivity(tmp_path):
    a = [{'file': write(tmp_path / 'a.vtu'), 'time': .4}]
    b = [{'file': write(tmp_path / 'b.vtu', 2.), 'time': .4}]
    csv_file = tmp_path / 'comparison.csv'
    rs.compare(a, b, 'V', mode='history', entity_index=0, csv_file=str(csv_file))
    with csv_file.open() as stream:
        row = list(csv.DictReader(stream))[0]
    assert row['reference_value_2'] == '1.0'
    assert row['candidate_value_2'] == '2.0'
    assert row['component_max_absolute_error_2'] == '1.0'
    mesh = meshio.read(b[0]['file'])
    mesh.cells[0].data = mesh.cells[0].data[:, ::-1]
    meshio.write(b[0]['file'], mesh)
    with pytest.raises(ValueError, match='connectivity'):
        rs.compare(a, b, 'T')


def test_invalid_selectors_and_shapes(tmp_path):
    path = write(tmp_path / 'a.vtu')
    records = [{'file': path, 'time': 1}]
    for options in ({}, {'point': [0, 0], 'entity_index': 0}, {'entity_index': -1},
                    {'point': [float('nan'), 0]}):
        with pytest.raises(ValueError):
            rs.history(records, 'T', **options)
    with pytest.raises(ValueError, match='physical time'):
        rs.records([{'file': path}])
    with pytest.raises(ValueError, match='Tolerances'):
        rs.compare(records, records, 'T', atol=-1)
    mesh = meshio.read(path)
    mesh.point_data['T'] = np.ones((3, 2))
    other = tmp_path / 'b.vtu'
    meshio.write(other, mesh)
    with pytest.raises(ValueError, match='shapes'):
        rs.compare(records, [{'file': str(other), 'time': 1}], 'T')
    with pytest.raises(ValueError, match='shape changed'):
        rs.history(records + [{'file': str(other), 'time': 2}], 'T', entity_index=0)
