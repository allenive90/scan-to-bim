"""Contract and real-PDAL integration checks for persisted preprocessing."""
import hashlib
import json
from pathlib import Path

import laspy
import numpy as np
import pytest
from pyproj import CRS, Transformer

from cloudlab.catalog import Catalog
from cloudlab.engine import CloudError, ROOT
from cloudlab.pdal_workbench import (
    MAX_NONSTREAM_POINTS, build_pipeline, driver_catalog, enqueue_run,
    get_run, list_runs, normalize_settings,
)
from cloudlab.worker import Worker


@pytest.fixture
def acquired(tmp_path):
    catalog = Catalog(tmp_path / 'archive')
    project = catalog.create_project('Edificio storico', 'Caposaldo locale', units='m')
    worker = Worker(catalog)
    catalog.enqueue(project, [ROOT / 'data/samples/campus.laz'], 'Facciata', {'reference_confirmed': True})
    assert worker.once()
    asset = catalog.asset(catalog.assets(project)[0]['id'])
    assert asset['status'] == 'READY'
    return catalog, project, worker, asset


def run(acquired, settings):
    catalog, project, worker, asset = acquired
    ident = enqueue_run(catalog, [asset['id']], settings)[0]
    assert get_run(catalog, ident)['status'] == 'QUEUED'
    assert worker.once()
    return get_run(catalog, ident)


def test_decimation_streams_full_input_and_persists_report(acquired):
    catalog, project, worker, asset = acquired
    result = run(acquired, {'reduction': 'decimation', 'decimation_step': 3})
    assert result['status'] == 'DONE', result['error']
    report = result['report']
    assert (report['points_input'], report['points_output']) == (60000, 20000)
    assert report['streaming'] is True
    assert report['reduction_percent'] == pytest.approx(66.667)
    output = Path(result['output_path'])
    assert output.is_absolute() and output.parent.name == result['id']
    assert hashlib.sha256(output.read_bytes()).hexdigest() == report['output_sha256']
    assert hashlib.sha256(Path(asset['raw_path']).read_bytes()).hexdigest() == asset['sha256']
    assert json.loads(Path(result['report_path']).read_text()) == report
    assert json.loads(Path(result['pipeline_path']).read_text()) == report['pipeline']
    with np.load(result['preview_path']) as preview:
        assert len(preview['X']) == 20000
        assert 'Classification' in preview
    assert list_runs(catalog, project)[0]['id'] == result['id']
    assert get_run(Catalog(catalog.root), result['id'])['status'] == 'DONE'
    assert not worker.once()


@pytest.mark.parametrize('output_format', ['las', 'laz', 'e57', 'ply'])
def test_real_export_formats(acquired, output_format):
    result = run(acquired, {'output_format': output_format, 'reduction': 'decimation', 'decimation_step': 10})
    assert result['status'] == 'DONE', result['error']
    assert Path(result['output_path']).suffix == f'.{output_format}'
    assert result['report']['points_output'] == 6000
    with np.load(result['preview_path']) as preview:
        assert len(preview['X']) == 6000


def test_crop_voxel_and_normals_survive_laz_export(acquired):
    result = run(acquired, {'z_range': [0, 5], 'reduction': 'voxel', 'voxel_size': .3, 'normals': True})
    assert result['status'] == 'DONE', result['error']
    report = result['report']
    assert 0 < report['points_output'] < 60000
    assert 0 <= report['bounds_output']['Z'][0] <= report['bounds_output']['Z'][1] <= 5
    assert report['streaming'] is False
    cloud = laspy.read(result['output_path'])
    assert 'NormalX' in cloud.point_format.dimension_names
    assert np.isfinite(cloud['NormalX']).all()
    with np.load(result['preview_path']) as preview:
        assert 'NormalX' in preview and 'Curvature' in preview


def test_outlier_removes_class_seven_and_is_not_only_annotation(acquired):
    result = run(acquired, {'outlier': True})
    assert result['status'] == 'DONE', result['error']
    assert result['report']['points_output'] < result['report']['points_input']
    cloud = laspy.read(result['output_path'])
    assert not np.any(np.asarray(cloud.classification) == 7)
    types = [stage['type'] for stage in result['report']['pipeline']['pipeline']]
    index = types.index('filters.outlier')
    assert result['report']['pipeline']['pipeline'][index + 1]['expression'] == 'Classification != 7'


def test_advanced_expression_and_feature_dimension(acquired):
    result = run(acquired, {'reduction': 'decimation', 'decimation_step': 20,
        'extra_filters': [{'type': 'filters.expression', 'expression': 'Z >= 0'},
                          {'type': 'filters.eigenvalues', 'knn': 8}]})
    assert result['status'] == 'DONE', result['error']
    with np.load(result['preview_path']) as preview:
        assert any(name.startswith('Eigenvalue') for name in preview.files)
    assert result['report']['points_output'] <= 3000


def test_reprojection_requires_valid_source_and_preserves_geographic_precision(acquired):
    catalog, _, _, asset = acquired
    with pytest.raises(CloudError, match='CRS'):
        enqueue_run(catalog, [asset['id']], {'output_crs': 'EPSG:4326'})
    result = run(acquired, {'input_crs': 'EPSG:32632', 'output_crs': 'EPSG:4326',
                            'reduction': 'decimation', 'decimation_step': 100})
    assert result['status'] == 'DONE', result['error']
    original = laspy.read(asset['raw_path'])
    output = laspy.read(result['output_path'])
    transform = Transformer.from_crs(32632, 4326, always_xy=True)
    lon, lat = transform.transform(original.x[::100], original.y[::100])
    assert np.allclose(output.x, lon, atol=1e-7, rtol=0)
    assert np.allclose(output.y, lat, atol=1e-7, rtol=0)
    assert output.header.parse_crs().equals(CRS.from_epsg(4326))


def test_input_crs_cannot_override_detected_crs(acquired):
    catalog, _, _, asset = acquired
    metadata = dict(asset['metadata'], file_crs='EPSG:32632')
    with catalog.connect() as db:
        db.execute('UPDATE assets SET metadata=? WHERE id=?', (json.dumps(metadata), asset['id']))
    with pytest.raises(CloudError, match='differisce'):
        enqueue_run(catalog, [asset['id']], {'input_crs': 'EPSG:4326'})


def test_review_local_operations_and_duplicate_resolution(acquired):
    catalog, project, worker, asset = acquired
    with catalog.connect() as db:
        db.execute("UPDATE assets SET status='REVIEW' WHERE id=?", (asset['id'],))
    catalog.enqueue(project, [ROOT / 'data/samples/campus.laz'], 'Duplicato')
    worker.once()
    duplicate = catalog.assets(project)[0]
    assert duplicate['status'] == 'DUPLICATE'
    ids = enqueue_run(catalog, [asset['id'], duplicate['id']], {})
    assert len(ids) == 1
    assert enqueue_run(catalog, [duplicate['id']], {}) == ids
    worker.once()
    result = get_run(catalog, ids[0])
    assert result['status'] == 'DONE', result['error']
    assert result['asset_id'] == asset['id']
    assert result['report']['warnings']


def test_memory_cap_is_server_enforced_on_input_before_reduction(acquired):
    catalog, _, _, asset = acquired
    metadata = dict(asset['metadata'], points=MAX_NONSTREAM_POINTS + 1)
    with catalog.connect() as db:
        db.execute('UPDATE assets SET metadata=? WHERE id=?', (json.dumps(metadata), asset['id']))
    with pytest.raises(CloudError, match='limite'):
        enqueue_run(catalog, [asset['id']], {'normals': True, 'reduction': 'decimation', 'decimation_step': 100})
    with pytest.raises(CloudError, match='intervallo'):
        enqueue_run(catalog, [asset['id']], {'nonstream_point_limit': 999999999})
    assert enqueue_run(catalog, [asset['id']], {'reduction': 'decimation', 'decimation_step': 100})


@pytest.mark.parametrize('settings', [
    {'extra_filters': [{'type': 'writers.text', 'filename': '/tmp/out.csv'}]},
    {'extra_filters': [{'type': 'filters.shell', 'command': 'id'}]},
    {'extra_filters': [{'type': 'filters.expression', 'expression': 'Z>0', 'option_file': '/tmp/options'}]},
    {'extra_filters': [{'type': 'filters.assign', 'value': 'X=0', 'log': '/tmp/out'}]},
    {'z_range': [float('nan'), 1]},
    {'output_crs': 'not-a-crs'},
    {'extra_filters': '[bad json'},
    {'extra_filters': [{'type': 'filters.normal', 'knn': 999999999}]},
])
def test_invalid_and_unsafe_settings_rejected(settings):
    with pytest.raises(CloudError):
        normalize_settings(settings)


def test_changed_original_fails_before_processing(acquired):
    catalog, _, worker, asset = acquired
    ident = enqueue_run(catalog, [asset['id']], {})[0]
    path = Path(asset['raw_path'])
    path.chmod(0o644)
    path.write_bytes(b'Changed by a third party')
    worker.once()
    result = get_run(catalog, ident)
    assert result['status'] == 'FAILED'
    assert 'Checksum' in result['error']
    assert result['output_path'] is None


def test_empty_output_has_clear_failure_and_original_remains_usable(acquired):
    catalog, _, _, asset = acquired
    result = run(acquired, {'z_range': [9999, 10000]})
    assert result['status'] == 'FAILED'
    assert 'Nessun punto' in result['error']
    assert catalog.asset(asset['id'])['status'] == 'READY'


def test_recovery_requeues_running_processing(acquired):
    catalog, _, worker, asset = acquired
    ident = enqueue_run(catalog, [asset['id']], {'reduction': 'decimation'})[0]
    with catalog.connect() as db:
        db.execute("UPDATE processing_runs SET status='RUNNING' WHERE id=?", (ident,))
    worker.recover()
    assert get_run(catalog, ident)['status'] == 'QUEUED'
    worker.once()
    assert get_run(catalog, ident)['status'] == 'DONE'


def test_installed_driver_catalog_and_allowlist():
    runtime = driver_catalog()
    names = {d['name'] for d in runtime['drivers']}
    assert {'readers.e57', 'writers.las', 'filters.normal'} <= names
    assert set(runtime['output_formats']) == {'las', 'laz', 'e57', 'ply'}
    assert 'filters.shell' not in runtime['advanced_filters']
