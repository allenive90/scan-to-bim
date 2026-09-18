"""Persistent PDAL preprocessing of archived scan-to-BIM assets.

Originals are checksum-verified and never written. Each run owns output, a sampled
preview, exact pipeline and provenance report. PDAL validates streaming support;
non-streaming operations are capped at two million *input* points, even when an
earlier reduction could shrink the dataset. This local worker is sequential, not
a distributed processing service. Advanced stages are a deliberately bounded,
path-free subset: the complete installed catalogue remains available to inspect.
E57 output is a flattened point cloud, not a reconstruction of original scans or
images. PLY does not carry a geodetic CRS. Both limitations appear in the report.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import time

import numpy as np
from pyproj import CRS

from cloudlab.catalog import now, uid
from cloudlab.engine import CloudError, las_writer, pdal_binary, reader, run_pdal

MAX_NONSTREAM_POINTS = 2_000_000
PREVIEW_LIMIT = 30_000
DEFAULT_SETTINGS = {
    "z_range": None, "reduction": "none", "decimation_step": 2,
    "voxel_size": 0.05, "outlier": False, "outlier_mean_k": 16,
    "outlier_multiplier": 2.0, "normals": False, "normal_knn": 16,
    "input_crs": "", "output_crs": "", "output_format": "laz",
    "extra_filters": [], "nonstream_point_limit": MAX_NONSTREAM_POINTS,
}
# Only listed options are accepted; notably no option_file, log, filename,
# inputs/tags, geometry datasource, Python or shell hooks can enter the pipeline.
ADVANCED_OPTIONS = {
    "filters.expression": {"expression"},
    "filters.range": {"limits"},
    "filters.assign": {"value"},
    "filters.ferry": {"dimensions"},
    "filters.transformation": {"matrix", "invert"},
    "filters.decimation": {"step", "offset"},
    "filters.sample": {"radius"},
    "filters.voxelcenternearestneighbor": {"cell"},
    "filters.voxelcentroidnearestneighbor": {"cell"},
    "filters.voxeldownsize": {"cell", "mode"},
    "filters.normal": {"knn", "radius", "always_up", "refine"},
    "filters.eigenvalues": {"knn", "radius", "normalize"},
    "filters.covariancefeatures": {"knn", "radius", "feature_set", "mode"},
    "filters.planefit": {"knn"},
    "filters.nndistance": {"k", "mode"},
    "filters.cluster": {"min_points", "max_points", "tolerance", "is3d"},
    "filters.dbscan": {"min_points", "eps", "dimensions"},
    "filters.smrf": {"cell", "cut", "scalar", "slope", "threshold", "window", "ignore", "returns"},
    "filters.csf": {"resolution", "threshold", "rigidness", "iterations", "smooth", "step", "returns"},
}
KNOWN_STREAMABLE = {
    "readers.las", "readers.e57", "writers.las", "writers.e57", "writers.text",
    "filters.expression", "filters.range", "filters.assign", "filters.ferry",
    "filters.transformation", "filters.decimation", "filters.stats", "filters.head",
    "filters.reprojection",
}


def ensure_schema(catalog):
    with catalog.connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS processing_runs (
                id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
                project_id TEXT NOT NULL REFERENCES projects(id),
                batch_id TEXT NOT NULL REFERENCES batches(id), name TEXT NOT NULL,
                status TEXT NOT NULL, phase TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                settings TEXT NOT NULL, report TEXT NOT NULL DEFAULT '{}',
                output_path TEXT, preview_path TEXT, pipeline_path TEXT, report_path TEXT,
                error TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS processing_queue ON processing_runs(status,created);
            CREATE INDEX IF NOT EXISTS processing_project ON processing_runs(project_id,batch_id);
        """)


def _decode(row):
    row = dict(row)
    row["settings"] = json.loads(row["settings"])
    row["report"] = json.loads(row["report"])
    return row


def list_runs(catalog, project_id, batch_id=""):
    ensure_schema(catalog)
    return [_decode(r) for r in catalog.rows("""SELECT * FROM processing_runs
        WHERE project_id=? AND (?='' OR batch_id=?) ORDER BY created DESC,rowid DESC""",
        (project_id, batch_id, batch_id))]


def get_run(catalog, run_id):
    ensure_schema(catalog)
    rows = catalog.rows("SELECT * FROM processing_runs WHERE id=?", (run_id,))
    if not rows:
        raise CloudError("Elaborazione PDAL non trovata.")
    return _decode(rows[0])


@lru_cache(maxsize=4)
def _drivers(binary):
    raw = run_pdal(["--drivers"], timeout=30)
    drivers = []
    for line in raw.splitlines():
        match = re.match(r"^(\w+\.\w+)\s+(.*)$", line)
        if match:
            name, description = match.groups()
            drivers.append({"name": name, "description": description,
                            "streamable": True if name in KNOWN_STREAMABLE else None})
        elif drivers and line.startswith(" "):
            drivers[-1]["description"] += " " + line.strip()
    names = {d["name"] for d in drivers}
    version = next((line.strip() for line in run_pdal(["--version"], timeout=30).splitlines()
                    if line.strip().startswith("pdal ")), "PDAL")
    return {"version": version, "drivers": drivers,
            "output_formats": [fmt for fmt, stage in (("laz", "writers.las"), ("las", "writers.las"),
                                 ("e57", "writers.e57"), ("ply", "writers.ply")) if stage in names],
            "advanced_filters": {name: sorted(options) for name, options in ADVANCED_OPTIONS.items() if name in names}}


def driver_catalog():
    """Installed drivers; streamability is confirmed per pipeline at execution."""
    return _drivers(pdal_binary())


def _number(value, label, minimum=None, maximum=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CloudError(f"{label}: indica un numero finito.")
    if integer and int(value) != value:
        raise CloudError(f"{label}: indica un numero intero.")
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise CloudError(f"{label}: valore fuori dall’intervallo consentito ({minimum}–{maximum}).")
    return int(value) if integer else float(value)


def normalize_settings(settings):
    if not isinstance(settings, dict) or set(settings) - set(DEFAULT_SETTINGS):
        raise CloudError("Impostazioni PDAL non riconosciute.")
    s = {**DEFAULT_SETTINGS, **settings}
    if not isinstance(s["reduction"], str) or s["reduction"] not in {"none", "decimation", "voxel"}:
        raise CloudError("Metodo di riduzione non valido.")
    s["decimation_step"] = _number(s["decimation_step"], "Passo di decimazione", 1, 1_000_000, True)
    s["voxel_size"] = _number(s["voxel_size"], "Dimensione voxel", 0.000001, 1_000_000)
    s["outlier_mean_k"] = _number(s["outlier_mean_k"], "Vicini per rumore", 2, 256, True)
    s["outlier_multiplier"] = _number(s["outlier_multiplier"], "Soglia rumore", 0.01, 100)
    s["normal_knn"] = _number(s["normal_knn"], "Vicini per normali", 3, 256, True)
    s["nonstream_point_limit"] = _number(s["nonstream_point_limit"], "Limite punti in memoria", 1, MAX_NONSTREAM_POINTS, True)
    for flag in ("outlier", "normals"):
        if type(s[flag]) is not bool:
            raise CloudError(f"Opzione {flag} non valida.")
    if s["z_range"] is not None:
        if not isinstance(s["z_range"], (list, tuple)) or len(s["z_range"]) != 2:
            raise CloudError("Intervallo Z non valido.")
        s["z_range"] = [_number(n, "Quota Z") for n in s["z_range"]]
        if s["z_range"][0] > s["z_range"][1]:
            raise CloudError("La quota minima deve essere minore o uguale alla massima.")
    for field in ("input_crs", "output_crs"):
        if not isinstance(s[field], str):
            raise CloudError("Il CRS deve essere una stringa, per esempio EPSG:32632.")
        if s[field].strip():
            try:
                s[field] = CRS.from_user_input(s[field].strip()).to_string()
            except Exception as exc:
                raise CloudError(f"CRS non valido: {s[field]}") from exc
        else:
            s[field] = ""
    runtime = driver_catalog()
    if not isinstance(s["output_format"], str) or s["output_format"] not in runtime["output_formats"]:
        raise CloudError("Formato di esportazione non disponibile in questo runtime PDAL.")
    extra = s["extra_filters"]
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except ValueError as exc:
            raise CloudError("Filtri avanzati: JSON non valido.") from exc
    if not isinstance(extra, list) or len(extra) > 20:
        raise CloudError("Filtri avanzati: usa una lista JSON di massimo 20 filtri.")
    normalized_extra = []
    available = {d["name"] for d in runtime["drivers"]}
    for stage in extra:
        if not isinstance(stage, dict) or not isinstance(stage.get("type"), str):
            raise CloudError("Ogni filtro avanzato deve avere un type.")
        name = stage["type"]
        if name not in ADVANCED_OPTIONS or name not in available:
            raise CloudError(f"Filtro avanzato non ammesso o non disponibile: {name}")
        if set(stage) - ADVANCED_OPTIONS[name] - {"type"}:
            raise CloudError(f"Opzioni non ammesse per {name}; percorsi e codice non sono consentiti.")
        for key, value in stage.items():
            values = value if isinstance(value, list) else [value]
            if any(not isinstance(v, (str, int, float, bool)) for v in values):
                raise CloudError("I parametri avanzati devono essere valori semplici.")
            if any(isinstance(v, float) and not math.isfinite(v) for v in values):
                raise CloudError("I parametri avanzati devono essere finiti.")
            if any(isinstance(v, str) and (len(v) > 8000 or "\x00" in v) for v in values):
                raise CloudError("Parametro avanzato troppo lungo o non valido.")
        # Validate expensive neighborhood parameters even for direct JSON use.
        for key in ("knn", "k", "min_points", "max_points", "iterations"):
            if key in stage:
                _number(stage[key], f"{name}.{key}", 1, 10000, True)
        for key in ("radius", "cell", "resolution", "eps", "tolerance"):
            if key in stage:
                _number(stage[key], f"{name}.{key}", 0.000001, 1_000_000)
        normalized_extra.append(dict(stage))
    s["extra_filters"] = normalized_extra
    return s


def _canonical(catalog, asset_id):
    seen = set()
    while asset_id not in seen:
        seen.add(asset_id)
        asset = catalog.asset(asset_id)
        if asset["status"] != "DUPLICATE":
            return asset
        asset_id = asset.get("duplicate_of")
        if not asset_id:
            break
    raise CloudError("Il rilievo duplicato non ha un originale valido.")


def _source_crs(asset, settings):
    meta = asset["metadata"]
    file_crs = meta.get("file_crs")
    declared = settings["input_crs"]
    if file_crs and declared and not CRS.from_user_input(file_crs).equals(CRS.from_user_input(declared)):
        raise CloudError("Il CRS sorgente dichiarato differisce dal CRS del file: non è possibile sovrascriverlo.")
    if file_crs or declared:
        return file_crs or declared
    declaration = meta.get("declaration", {})
    for value in (declaration.get("crs"), declaration.get("reference")):
        if value:
            try:
                return CRS.from_user_input(value).to_string()
            except Exception:
                pass
    return ""


def _validate_asset(asset, s):
    if asset["status"] not in {"READY", "REVIEW"}:
        raise CloudError(f"{asset['name']}: completa prima l’acquisizione o la conversione del rilievo.")
    if not asset.get("raw_path") or not asset.get("sha256"):
        raise CloudError("Originale archiviato o checksum non disponibile.")
    source_crs = _source_crs(asset, s)
    if s["output_crs"] and not source_crs:
        raise CloudError("Per riproiettare serve il CRS del file o una dichiarazione CRS sorgente valida.")
    points = asset["metadata"].get("points", 0)
    if not isinstance(points, (int, float)) or points <= 0:
        raise CloudError("Il conteggio dei punti verificati non è disponibile.")
    potentially_nonstream = s["reduction"] == "voxel" or s["outlier"] or s["normals"] or s["output_format"] == "ply"
    potentially_nonstream |= any(stage["type"] not in KNOWN_STREAMABLE for stage in s["extra_filters"])
    if potentially_nonstream and points > s["nonstream_point_limit"]:
        raise CloudError(f"{asset['name']}: {int(points):,} punti superano il limite di {s['nonstream_point_limit']:,} "
                         "per gli algoritmi in memoria. Suddividi il rilievo o usa filtri streaming; non viene campionato l’output.")


def enqueue_run(catalog, asset_ids, settings):
    ensure_schema(catalog)
    if not isinstance(asset_ids, list) or not asset_ids or len(asset_ids) > 5000:
        raise CloudError("Seleziona da 1 a 5.000 rilievi acquisiti.")
    s = normalize_settings(settings)
    # Resolve each duplicate and enqueue its canonical cloud only once.
    assets = {a["id"]: a for a in (_canonical(catalog, ident) for ident in asset_ids)}
    if len({a["project_id"] for a in assets.values()}) != 1:
        raise CloudError("Seleziona rilievi dello stesso progetto.")
    for asset in assets.values():
        _validate_asset(asset, s)
    encoded = json.dumps(s, sort_keys=True, ensure_ascii=False)
    result = []
    with catalog.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for asset in assets.values():
            # Double clicks reuse an existing queued/running identical request.
            existing = db.execute("SELECT id FROM processing_runs WHERE asset_id=? AND settings=? AND status IN ('QUEUED','RUNNING')",
                                  (asset["id"], encoded)).fetchone()
            if existing:
                result.append(existing["id"])
                continue
            run_id, stamp = uid(), now()
            db.execute("""INSERT INTO processing_runs(id,asset_id,project_id,batch_id,name,status,phase,settings,created,updated)
                VALUES (?,?,?,?,?,'QUEUED','In attesa di elaborazione',?,?,?)""",
                (run_id, asset["id"], asset["project_id"], asset["batch_id"], asset["name"], encoded, stamp, stamp))
            catalog.event(db, asset["id"], "PROCESSING_QUEUED", f"Elaborazione PDAL {run_id}")
            result.append(run_id)
    return result


def recover_runs(catalog):
    ensure_schema(catalog)
    with catalog.connect() as db:
        db.execute("UPDATE processing_runs SET status='QUEUED',phase='Ripresa dopo interruzione',progress=0,updated=? WHERE status='RUNNING'", (now(),))


def _update(catalog, run_id, phase, progress):
    with catalog.connect() as db:
        db.execute("UPDATE processing_runs SET phase=?,progress=?,updated=? WHERE id=?", (phase, progress, now(), run_id))


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_pipeline(asset, settings, target):
    s = settings
    source_crs = _source_crs(asset, s)
    read = reader(Path(asset["raw_path"]))
    if source_crs and not asset["metadata"].get("file_crs"):
        read["override_srs"] = source_crs
    stages = [read, {"type": "filters.stats", "dimensions": "X,Y,Z", "tag": "input_stats"}]
    if "Omit" in asset["metadata"].get("dimensions", []):
        stages.append({"type": "filters.expression", "expression": "Omit == 0"})
    if s["z_range"] is not None:
        lo, hi = s["z_range"]
        stages.append({"type": "filters.expression", "expression": f"Z >= {lo} && Z <= {hi}"})
    if s["outlier"]:
        stages.extend([{"type": "filters.outlier", "method": "statistical", "mean_k": s["outlier_mean_k"], "multiplier": s["outlier_multiplier"]},
                       {"type": "filters.expression", "expression": "Classification != 7"}])
    if s["reduction"] == "decimation":
        stages.append({"type": "filters.decimation", "step": s["decimation_step"]})
    elif s["reduction"] == "voxel":
        stages.append({"type": "filters.voxelcenternearestneighbor", "cell": s["voxel_size"]})
    if s["output_crs"]:
        stages.append({"type": "filters.reprojection", "in_srs": source_crs, "out_srs": s["output_crs"], "error_on_failure": True})
    if s["normals"]:
        stages.append({"type": "filters.normal", "knn": s["normal_knn"]})
    stages.extend(s["extra_filters"])
    stages.append({"type": "filters.stats", "dimensions": "X,Y,Z", "tag": "output_stats"})
    if s["output_format"] in {"las", "laz"}:
        write = las_writer(target)
        write["forward"] = "all"
        effective_crs = s["output_crs"] or source_crs
        if effective_crs and CRS.from_user_input(effective_crs).is_geographic:
            write.update(scale_x=0.0000001, scale_y=0.0000001)
    elif s["output_format"] == "e57":
        write = {"type": "writers.e57", "filename": str(target), "double_precision": True}
    else:
        write = {"type": "writers.ply", "filename": str(target), "storage_mode": "little endian"}
    stages.append(write)
    return {"pipeline": stages}


def _statistics(metadata, tag):
    stages = metadata.get("stages", {})
    node = stages.get(tag)
    if node is None:
        # PDAL records tag names as a field on each filter's metadata node.
        for value in stages.values():
            for candidate in value if isinstance(value, list) else [value]:
                if isinstance(candidate, dict) and candidate.get("tag") == tag:
                    node = candidate
                    break
    if node is None:
        stats = stages.get("filters.stats")
        if isinstance(stats, list):
            node = stats[0 if tag == "input_stats" else -1]
        elif isinstance(stats, dict):
            node = stats
    if not node:
        raise CloudError("Statistiche PDAL della pipeline non disponibili.")
    xyz = {item["name"]: item for item in node.get("statistic", []) if item["name"] in {"X", "Y", "Z"}}
    if set(xyz) != {"X", "Y", "Z"}:
        raise CloudError("Statistiche XYZ incomplete.")
    count = int(xyz["X"]["count"])
    if count == 0:
        return 0, {}
    bounds = {axis: [float(stat["minimum"]), float(stat["maximum"])] for axis, stat in xyz.items()}
    if not all(math.isfinite(v) for limits in bounds.values() for v in limits):
        raise CloudError("La pipeline ha prodotto coordinate non finite.")
    return count, bounds


def _preview(output, folder, points):
    header = json.loads(run_pdal(["info", "--schema", "--metadata", str(output)], timeout=120))
    names = [dim["name"] for dim in header["schema"]["dimensions"]]
    dims = list(dict.fromkeys(["X", "Y", "Z", *names]))
    preview_reader = {"type": "readers.ply", "filename": str(output)} if output.suffix == ".ply" else reader(output)
    stages = [preview_reader, {"type": "filters.stats", "dimensions": "X,Y,Z", "tag": "delivered_stats"},
              {"type": "filters.decimation", "step": max(1, math.ceil(points / PREVIEW_LIMIT))},
              {"type": "filters.head", "count": PREVIEW_LIMIT},
              {"type": "writers.text", "filename": str(folder / "preview.csv"), "order": ",".join(dims),
               "keep_unspecified": False, "precision": 8}]
    pipeline = folder / "preview-pipeline.json"
    pipeline.write_text(json.dumps({"pipeline": stages}, indent=2))
    validation = json.loads(run_pdal(["pipeline", str(pipeline), "--validate"], timeout=120))
    preview_metadata = folder / "output-validation.json"
    run_pdal(["pipeline", str(pipeline), "--stream" if validation["streamable"] else "--nostream",
              "--metadata", str(preview_metadata)], timeout=7200)
    delivered_count, delivered_bounds = _statistics(json.loads(preview_metadata.read_text()), "delivered_stats")
    if delivered_count != points:
        raise CloudError("Il file esportato contiene un numero di punti diverso dal risultato della pipeline.")
    data = np.atleast_1d(np.genfromtxt(folder / "preview.csv", delimiter=",", names=True, dtype=float))
    preview = {name: data[name] for name in dims}
    for name in ("Red", "Green", "Blue", "Intensity", "Classification"):
        preview.setdefault(name, np.zeros(len(data)))
    path = folder / "preview.npz"
    np.savez_compressed(path, **preview)
    (folder / "preview.csv").unlink(missing_ok=True)
    return path, len(data), header, delivered_bounds


def execute_run(catalog, run):
    started = time.monotonic()
    asset = catalog.asset(run["asset_id"])
    s = normalize_settings(run["settings"])
    _validate_asset(asset, s)
    _update(catalog, run["id"], "Verifica integrità dell’originale", 5)
    raw = Path(asset["raw_path"])
    if _digest(raw) != asset["sha256"]:
        raise CloudError("Checksum dell’originale incoerente: ripristina il file archiviato prima di elaborarlo.")
    folder = catalog.root / "processing" / run["id"]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"processed.{s['output_format']}"
    pipeline_path, metadata_path = folder / "pipeline.json", folder / "pdal-metadata.json"
    pipeline = build_pipeline(asset, s, target)
    pipeline_path.write_text(json.dumps(pipeline, indent=2, ensure_ascii=False))
    validation = json.loads(run_pdal(["pipeline", str(pipeline_path), "--validate"], timeout=120))
    if not validation.get("valid"):
        raise CloudError(f"Pipeline PDAL non valida: {validation.get('error_detail', '')}")
    streaming = bool(validation["streamable"])
    if not streaming and asset["metadata"]["points"] > s["nonstream_point_limit"]:
        raise CloudError("La pipeline richiede elaborazione in memoria e supera il limite punti configurato.")
    _update(catalog, run["id"], "Elaborazione PDAL in streaming" if streaming else "Elaborazione PDAL in memoria", 20)
    run_pdal(["pipeline", str(pipeline_path), "--stream" if streaming else "--nostream", "--metadata", str(metadata_path)], timeout=7200)
    metadata = json.loads(metadata_path.read_text())
    input_points, input_bounds = _statistics(metadata, "input_stats")
    output_points, output_bounds = _statistics(metadata, "output_stats")
    if input_points != asset["metadata"]["points"]:
        raise CloudError("Il conteggio dei punti sorgenti è cambiato rispetto all’acquisizione.")
    if output_points <= 0:
        raise CloudError("Nessun punto dopo i filtri: amplia l’intervallo o riduci le soglie e avvia una nuova elaborazione.")
    _update(catalog, run["id"], "Anteprima 3D e riepilogo risultati", 80)
    preview_path, preview_points, output_header, delivered_bounds = _preview(target, folder, output_points)
    warnings = []
    if asset["status"] == "REVIEW":
        warnings.append("Riferimento del rilievo da verificare prima dell’utilizzo BIM; sono consentite operazioni locali.")
    if s["outlier"]:
        warnings.append("Rimossi i punti classificati 7 dopo il filtro statistico, inclusi eventuali punti già in classe 7.")
    if s["output_format"] == "e57":
        warnings.append("E57 derivato con una sola nuvola: struttura scansioni, immagini e campi non supportati dal writer restano nell’originale.")
    if s["output_format"] == "ply":
        warnings.append("PLY non conserva il CRS geodetico: usa il riferimento registrato in questo report.")
    if s["normals"] and "NormalX" not in [dim["name"] for dim in output_header["schema"]["dimensions"]]:
        warnings.append("Il formato scelto non conserva le normali calcolate; esporta LAS/LAZ o PLY per conservarle.")
    report = {"schema_version": "scan-to-bim.pdal/1", "run_id": run["id"], "asset_id": asset["id"],
              "source_name": asset["name"], "source_sha256": asset["sha256"], "source_path": str(raw),
              "output_path": str(target), "output_sha256": _digest(target), "output_bytes": target.stat().st_size,
              "points_input": input_points, "points_output": output_points,
              "points_removed": input_points - output_points, "reduction_percent": round(100 * (1 - output_points / input_points), 3),
              "bounds_input": input_bounds, "bounds_output": delivered_bounds,
              "bounds_before_encoding": output_bounds, "preview_points": preview_points,
              "elapsed_seconds": round(time.monotonic() - started, 3), "streaming": streaming,
              "input_crs": _source_crs(asset, s) or None, "output_crs": s["output_crs"] or _source_crs(asset, s) or None,
              "settings": s, "pdal_version": driver_catalog()["version"], "pipeline": pipeline,
              "metadata": metadata, "output_header": output_header, "warnings": warnings,
              "created": now()}
    report_path = folder / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    with catalog.connect() as db:
        db.execute("""UPDATE processing_runs SET status='DONE',phase='Elaborazione completata',progress=100,
            report=?,output_path=?,preview_path=?,pipeline_path=?,report_path=?,error='',updated=? WHERE id=?""",
            (json.dumps(report), str(target), str(preview_path), str(pipeline_path), str(report_path), now(), run["id"]))
        catalog.event(db, asset["id"], "PROCESSING_DONE", f"PDAL {run['id']}: {input_points} → {output_points} punti")


def process_next(catalog):
    ensure_schema(catalog)
    with catalog.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM processing_runs WHERE status='QUEUED' ORDER BY created,rowid LIMIT 1").fetchone()
        if not row:
            return False
        run = _decode(row)
        db.execute("UPDATE processing_runs SET status='RUNNING',phase='Avvio PDAL',progress=0,updated=? WHERE id=?", (now(), run["id"]))
    try:
        execute_run(catalog, run)
    except Exception as exc:
        with catalog.connect() as db:
            db.execute("UPDATE processing_runs SET status='FAILED',phase='Elaborazione non riuscita',error=?,updated=? WHERE id=?",
                       (str(exc)[-4000:], now(), run["id"]))
            catalog.event(db, run["asset_id"], "PROCESSING_FAILED", f"{run['id']}: {str(exc)[-2000:]}")
    return True
