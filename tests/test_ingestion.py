import hashlib
import json
from pathlib import Path

import laspy
import numpy as np
import pytest
from pyproj import CRS

from cloudlab.catalog import Catalog, discover
from cloudlab.engine import ROOT, CloudError
from cloudlab.worker import Worker


@pytest.fixture
def setup(tmp_path):
    catalog = Catalog(tmp_path / "archive")
    project = catalog.create_project("Edificio A", "Riferimento locale A", units="m")
    return catalog, project, Worker(catalog)


def ingest(setup, paths, confirmed=True, parent=None):
    c, p, w = setup
    batch = c.enqueue(p, paths, "Rilievo", {"reference_confirmed": confirmed}, parent_id=parent)
    while w.once():
        pass
    return [c.asset(a["id"]) for a in c.assets(p) if a["batch_id"] == batch]


@pytest.mark.parametrize("suffix", ["las", "laz", "e57"])
def test_ingestion_persists_original_and_full_metadata(setup, suffix):
    c, p, w = setup
    source = ROOT / f"data/samples/campus.{suffix}"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    a = ingest(setup, [source])[0]
    assert a["status"] == "READY", a["error"]
    assert a["sha256"] == digest
    assert hashlib.sha256(Path(a["raw_path"]).read_bytes()).hexdigest() == digest
    assert a["metadata"]["points"] == 60000
    assert a["metadata"]["validation"] == "full_streaming_read"
    with np.load(a["preview_path"]) as data:
        assert len(data["X"]) <= 30000
    assert Catalog(c.root).asset(a["id"])["status"] == "READY"
    assert source.read_bytes() == Path(a["raw_path"]).read_bytes()


def test_missing_reference_blocks_handoff_until_confirmed(setup):
    c, p, _ = setup
    a = ingest(setup, [ROOT / "data/samples/terrain.laz"], confirmed=False)[0]
    assert a["status"] == "REVIEW"
    with pytest.raises(CloudError, match="pronti"):
        c.delivery(p, [a["id"]])
    c.confirm_reference(a["id"], "Caposaldo A", "m", "Verificato con il fornitore")
    path = c.delivery(p, [a["id"]])
    manifest = json.loads(path.read_text())
    assert manifest["next_stage"] == "pdal_preprocessing"
    assert manifest["assets"][0]["sha256"] == a["sha256"]
    assert c.rows("SELECT action FROM events WHERE asset_id=? ORDER BY id", (a["id"],))[-1]["action"] == "HANDOFF"


def test_duplicates_are_project_scoped(setup):
    c, p, w = setup
    source = ROOT / "data/samples/tunnel.las"
    original = ingest(setup, [source])[0]
    duplicate = ingest(setup, [source])[0]
    assert duplicate["status"] == "DUPLICATE"
    assert duplicate["duplicate_of"] == original["id"]
    assert duplicate["raw_path"] == original["raw_path"]
    p2 = c.create_project("Edificio B", "Caposaldo B")
    independent = ingest((c, p2, w), [source])[0]
    assert independent["status"] == "READY"


def test_failure_is_isolated_and_retry_works(setup, tmp_path):
    c, p, w = setup
    bad = tmp_path / "damaged.laz"
    bad.write_bytes(b"invalid file")
    results = ingest(setup, [bad, ROOT / "data/samples/campus.laz"])
    assert sorted(a["status"] for a in results) == ["FAILED", "READY"]
    failed = next(a for a in results if a["status"] == "FAILED")
    bad.write_bytes((ROOT / "data/samples/tunnel.laz").read_bytes())
    c.retry(failed["id"])
    w.once()
    assert c.asset(failed["id"])["status"] == "READY"
    assert c.rows("SELECT attempts FROM jobs WHERE asset_id=?", (failed["id"],))[0]["attempts"] == 2


def test_truncated_payload_fails_full_validation(setup, tmp_path):
    path = tmp_path / "truncated.las"
    path.write_bytes((ROOT / "data/samples/campus.las").read_bytes()[:-3600])
    asset = ingest(setup, [path])[0]
    assert asset["status"] == "FAILED"


def test_autodesk_archived_and_derivative_linked(setup, tmp_path):
    c, p, w = setup
    path = tmp_path / "fixture.rcp"
    path.write_bytes(b"Test-only placeholder: proprietary payload is not parsed")
    parent = ingest(setup, [path])[0]
    assert parent["status"] == "CONVERSION"
    assert Path(parent["raw_path"]).read_bytes() == path.read_bytes()
    derivative = ingest(setup, [ROOT / "data/samples/campus.e57"], parent=parent["id"])[0]
    assert derivative["status"] == "READY"
    assert derivative["parent_id"] == parent["id"]
    with pytest.raises(CloudError):
        c.enqueue(p, [ROOT / "data/samples/campus.las"], "invalid", parent_id=parent["id"])


def test_interrupted_job_recovery(setup):
    c, p, w = setup
    c.enqueue(p, [ROOT / "data/samples/tunnel.e57"], "Interrupted", {"reference_confirmed": True})
    with c.connect() as db:
        db.execute("UPDATE jobs SET status='RUNNING'")
        db.execute("UPDATE assets SET status='RUNNING'")
    w.recover()
    w.once()
    assert c.assets(p)[0]["status"] == "READY"
    assert c.rows("SELECT * FROM events WHERE action='RECOVERED'")


def test_discovery_and_isolation(setup, tmp_path):
    c, p, _ = setup
    folder = tmp_path / "survey"
    folder.mkdir()
    (folder / "ignored.txt").write_text("not a cloud")
    (folder / "scan.LAS").write_bytes(b"fixture")
    (folder / "sub").mkdir()
    (folder / "sub/scan.e57").write_bytes(b"fixture")
    assert len(discover(folder, recursive=False)) == 1
    assert len(discover(folder, recursive=True)) == 2
    with pytest.raises(CloudError):
        discover(c.root, excluded=c.root)


def test_georeferenced_crs_cannot_be_overridden(setup, tmp_path):
    c, p, _ = setup
    source = tmp_path / "utm.las"
    cloud = laspy.read(ROOT / "data/samples/campus.las")
    cloud.header.add_crs(CRS.from_epsg(32632))
    cloud.write(source)
    a = ingest(setup, [source])[0]
    assert a["status"] == "REVIEW"  # local project and georeferenced source disagree
    assert a["metadata"]["file_crs"] == "EPSG:32632"
    with pytest.raises(CloudError):
        c.confirm_reference(a["id"], "EPSG:4326", "degree", "Incorrect override")
    with pytest.raises(CloudError):
        c.confirm_reference(a["id"], "EPSG:32632", "mm", "Incorrect units")
    c.confirm_reference(a["id"], "EPSG:32632", "m", "Source CRS verified; reproject later")
    assert c.asset(a["id"])["status"] == "READY"


def test_modified_archive_is_not_handed_off(setup):
    c, p, _ = setup
    asset = ingest(setup, [ROOT / "data/samples/campus.laz"])[0]
    raw = Path(asset["raw_path"])
    raw.chmod(0o644)
    raw.write_bytes(b"changed outside the application")
    with pytest.raises(CloudError, match="cambiato"):
        c.delivery(p, [asset["id"]])


def test_project_units_are_consistent(setup):
    c, _, _ = setup
    with pytest.raises(CloudError, match="unità"):
        c.create_project("bad", "reference", "EPSG:4326", "m")


def test_browser_staging_preserves_name_and_cleans_acquired_upload(setup):
    import io
    from cloudlab.ingestion_ui import stage_uploads
    c, p, w = setup
    uploaded = io.BytesIO((ROOT / "data/samples/campus.laz").read_bytes())
    uploaded.name = "../../campus.laz"
    uploaded.size = len(uploaded.getvalue())
    paths = stage_uploads(c, [uploaded])
    assert paths[0].is_relative_to(c.root / "inbox")
    assert paths[0].name == "campus.laz"
    asset = ingest(setup, paths)[0]
    assert asset["status"] == "READY"
    assert not paths[0].exists()
    assert Path(asset["source"]).is_file()
