import io
from pathlib import Path

import laspy
import numpy as np
import pytest
from pyproj import CRS

from cloudlab.engine import CloudError, ROOT, build_pipeline, execute, process


@pytest.mark.parametrize("scene", ["campus", "terrain", "tunnel"])
@pytest.mark.parametrize("suffix", ["las", "laz", "e57"])
def test_real_formats_preserve_points(scene, suffix):
    result = process((ROOT / f"data/samples/{scene}.{suffix}").read_bytes(), f".{suffix}", preview_limit=1000)
    cloud = laspy.read(io.BytesIO(result["output"]))
    assert len(cloud.points) == 60000
    assert result["summary"]["preview_points"] == 1000
    assert np.isfinite(result["preview"]["X"]).all()
    assert max(cloud.red) > 0
    original = laspy.read(ROOT / f"data/samples/{scene}.las")
    # The E57 reader may reorder points: compare bounds, not input row indices.
    np.testing.assert_allclose(cloud.header.mins, original.header.mins, atol=.002)
    np.testing.assert_allclose(cloud.header.maxs, original.header.maxs, atol=.002)


def test_filters_apply_to_full_export_and_preview_is_independent():
    source = (ROOT / "data/samples/campus.laz").read_bytes()
    clipped = process(source, ".laz", voxel=.4, z_range=(2, 6), preview_limit=50, output_format="las")
    cloud = laspy.read(io.BytesIO(clipped["output"]))
    assert 50 < len(cloud.points) < 60000
    assert len(clipped["preview"]["X"]) == 50
    assert min(cloud.z) >= 2 and max(cloud.z) <= 6
    assert clipped["pipeline"]["pipeline"][0]["filename"] == "input.laz"


@pytest.mark.parametrize("suffix", [".rcp", ".rcs", ".RCS"])
def test_autodesk_requires_real_conversion(suffix):
    with pytest.raises(CloudError, match="ReCap"):
        process(b"not a converted point cloud", suffix)


def test_invalid_file_and_empty_crop_are_reported():
    with pytest.raises(CloudError):
        process(b"corrupt LAS", ".las")
    with pytest.raises(CloudError, match="Nessun punto"):
        process((ROOT / "data/samples/campus.las").read_bytes(), ".las", z_range=(1000, 2000))


def test_invalid_filter_rejected():
    with pytest.raises(CloudError, match="Intervallo"):
        build_pipeline(Path("in.las"), Path("out.laz"), z_range=(10, -1))
    with pytest.raises(CloudError, match="voxel"):
        build_pipeline(Path("in.las"), Path("out.laz"), voxel=float("nan"))


def test_crs_and_extra_dimension_survive_export():
    cloud = laspy.read(ROOT / "data/samples/campus.las")
    cloud.header.add_crs(CRS.from_epsg(32632))
    cloud.header.offsets += [500000, 4500000, 0]
    cloud.points.offsets = cloud.header.offsets
    cloud.add_extra_dim(laspy.ExtraBytesParams(name="Confidence", type="float32"))
    cloud.Confidence = np.full(len(cloud.points), .75, dtype=np.float32)
    source = io.BytesIO()
    cloud.write(source)
    result = process(source.getvalue(), ".las", preview_limit=10)
    output = laspy.read(io.BytesIO(result["output"]))
    assert output.header.parse_crs().to_epsg() == 32632
    np.testing.assert_allclose(output.Confidence, .75)
    np.testing.assert_allclose(output.x, cloud.x, atol=.001)


def test_e57_invalid_point_flag_is_honoured(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("X,Y,Z,Omit\n0,0,0,0\n1,1,1,1\n2,2,2,0\n")
    e57 = tmp_path / "invalid.e57"
    execute([{"type": "readers.text", "filename": str(source)},
             {"type": "writers.e57", "filename": str(e57)}], tmp_path)
    result = process(e57.read_bytes(), ".e57")
    assert result["summary"]["points"] == 2
    assert 1 not in result["preview"]["X"]
