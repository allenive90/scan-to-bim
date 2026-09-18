"""Independent readback checks for the restoration-only example collection."""
import io
import json

import laspy
import numpy as np
import pytest

from cloudlab.engine import ROOT, process
from cloudlab.restoration_samples import OUTLIER_COUNT, POINT_COUNT, SCENES, make_scene


@pytest.mark.parametrize("name", SCENES)
def test_restoration_scene_is_finite_reproducible_and_has_declared_noise(name):
    points = make_scene(name)
    assert points.shape == (POINT_COUNT, 8)
    assert np.isfinite(points).all()
    assert np.all(np.ptp(points[:, :3], axis=0) > 1)
    assert np.all((points[:, 3:6] >= 0) & (points[:, 3:6] <= 65535))
    assert np.all(np.ptp(points[:, 3:6], axis=0) > 5000)
    assert np.count_nonzero(points[:, 7] == 7) == OUTLIER_COUNT
    np.testing.assert_array_equal(points, make_scene(name))


@pytest.mark.parametrize("name", SCENES)
@pytest.mark.parametrize("suffix", ["las", "laz", "e57"])
def test_restoration_formats_are_readable_with_rgb_and_same_bounds(name, suffix):
    path = ROOT / "data/restoration" / f"{name}.{suffix}"
    result = process(path.read_bytes(), f".{suffix}", preview_limit=100)
    output = laspy.read(io.BytesIO(result["output"]))
    reference = laspy.read(ROOT / "data/restoration" / f"{name}.las")
    assert output.header.point_count == POINT_COUNT
    assert np.isfinite(output.xyz).all()
    assert max(output.red) > 0 and max(output.green) > 0 and max(output.blue) > 0
    np.testing.assert_allclose(output.header.mins, reference.header.mins, atol=.002)
    np.testing.assert_allclose(output.header.maxs, reference.header.maxs, atol=.002)
    assert output.header.parse_crs() is None


def test_restoration_manifest_is_explicit_about_synthetic_provenance():
    manifest = json.loads((ROOT / "data/restoration/manifest.json").read_text())
    assert manifest["synthetic"] is True
    assert manifest["units"] == "m"
    assert "non costituiscono diagnosi" in manifest["limitations"]
    assert {scene["id"] for scene in manifest["scenes"]} == set(SCENES)
    for scene in manifest["scenes"]:
        assert scene["synthetic"] and scene["points"] == POINT_COUNT
        assert len(scene["files"]) == 3
        assert all(item["reader_verified"] for item in scene["validation"].values())


def test_unrelated_scene_is_not_offered():
    with pytest.raises(ValueError, match="Scena sconosciuta"):
        make_scene("terrain")
