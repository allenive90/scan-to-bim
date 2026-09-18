from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import laspy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DIRECT_FORMATS = {".e57", ".las", ".laz"}
AUTODESK_FORMATS = {".rcp", ".rcs"}
MAX_BYTES = 200 * 1024 * 1024


class CloudError(RuntimeError):
    pass


def pdal_binary() -> str:
    configured = os.environ.get("PDAL_BIN")
    found = configured or shutil.which("pdal")
    local = ROOT / ".env/bin/pdal"
    if not found and local.is_file():
        found = str(local)
    if not found:
        raise CloudError("PDAL non trovato. Crea l’ambiente descritto nel README e avvia di nuovo l’app.")
    return found


def run_pdal(args: list[str], timeout: int = 180) -> str:
    try:
        result = subprocess.run([pdal_binary(), *args], capture_output=True, text=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise CloudError(f"PDAL ha superato il limite di {timeout} secondi. Riduci il file di ingresso.") from exc
    except OSError as exc:
        raise CloudError(f"Impossibile avviare PDAL: {exc}") from exc
    if result.returncode:
        raise CloudError((result.stderr or result.stdout or "Errore PDAL")[-4000:])
    return result.stdout.strip()


def capabilities() -> dict:
    drivers = run_pdal(["--drivers"], timeout=30)
    version = next((line.strip() for line in run_pdal(["--version"], timeout=30).splitlines() if line.strip().startswith("pdal ")), "PDAL")
    return {"version": version,
            "las": "readers.las" in drivers and "writers.las" in drivers,
            "e57": "readers.e57" in drivers and "writers.e57" in drivers}


def execute(stages: list[dict], folder: Path) -> dict:
    pipeline_path, metadata_path = folder / "pipeline.json", folder / "metadata.json"
    pipeline_path.write_text(json.dumps({"pipeline": stages}, indent=2))
    run_pdal(["pipeline", str(pipeline_path), "--metadata", str(metadata_path)])
    return json.loads(metadata_path.read_text())


def reader(path: Path) -> dict:
    suffix = path.suffix.lower()
    if suffix in AUTODESK_FORMATS:
        raise CloudError("RCP/RCS richiedono Autodesk ReCap: esporta il progetto in E57 e carica il file esportato.")
    if suffix not in DIRECT_FORMATS:
        raise CloudError("Formato non supportato: usa E57, LAS o LAZ.")
    return {"type": "readers.e57" if suffix == ".e57" else "readers.las", "filename": str(path)}


def las_writer(path: Path) -> dict:
    return {"type": "writers.las", "filename": str(path), "minor_version": 4,
            "dataformat_id": 7, "compression": path.suffix.lower() == ".laz",
            "scale_x": 0.001, "scale_y": 0.001, "scale_z": 0.001,
            "offset_x": "auto", "offset_y": "auto", "offset_z": "auto",
            "extra_dims": "all"}


def build_pipeline(source: Path, target: Path, voxel: float = 0,
                   z_range: tuple[float, float] | None = None, has_omit: bool = False) -> list[dict]:
    if not math.isfinite(voxel) or voxel < 0:
        raise CloudError("La dimensione del voxel deve essere un numero positivo o zero.")
    stages = [reader(source)]
    if has_omit:
        stages.append({"type": "filters.expression", "expression": "Omit == 0"})
    stages.append({"type": "filters.stats", "dimensions": "X,Y,Z"})
    if z_range is not None:
        low, high = z_range
        if not all(map(math.isfinite, z_range)) or low > high:
            raise CloudError("Intervallo Z non valido: il minimo deve essere minore o uguale al massimo.")
        stages.append({"type": "filters.expression", "expression": f"Z >= {low} && Z <= {high}"})
    if voxel:
        stages.append({"type": "filters.voxelcenternearestneighbor", "cell": voxel})
    stages.append(las_writer(target))
    return stages


def read_preview(path: Path, limit: int) -> tuple[dict, dict]:
    """Uniform deterministic sample of the whole output, with bounded Python memory."""
    with laspy.open(path) as cloud:
        header = cloud.header
        total = header.point_count
        if not total:
            raise CloudError("Nessun punto dopo i filtri. Amplia l’intervallo Z o disattiva il filtro.")
        chosen = np.sort(np.random.default_rng(42).choice(total, min(total, limit), replace=False))
        names = ["X", "Y", "Z", "Red", "Green", "Blue", "Intensity", "Classification"]
        columns: dict[str, list] = {name: [] for name in names}
        offset = 0
        for chunk in cloud.chunk_iterator(250_000):
            local = chosen[(chosen >= offset) & (chosen < offset + len(chunk))] - offset
            if len(local):
                values = [chunk.x, chunk.y, chunk.z, chunk.red, chunk.green, chunk.blue,
                          chunk.intensity, chunk.classification]
                for name, values_column in zip(names, values):
                    columns[name].append(np.asarray(values_column)[local])
            offset += len(chunk)
        preview = {name: np.concatenate(parts) for name, parts in columns.items()}
        crs = header.parse_crs()
        summary = {"points": total, "preview_points": len(chosen),
                   "minimum": header.mins.tolist(), "maximum": header.maxs.tolist(),
                   "crs": crs.to_string() if crs else "Non dichiarato · verificare unità e riferimento",
                   "dimensions": list(header.point_format.dimension_names)}
    return preview, summary


def process(data: bytes, suffix: str, voxel: float = 0,
            z_range: tuple[float, float] | None = None, preview_limit: int = 30_000,
            output_format: str = "laz") -> dict:
    if not data or len(data) > MAX_BYTES:
        raise CloudError("Carica un file non vuoto di dimensione massima 200 MB.")
    suffix = suffix.lower()
    if suffix not in DIRECT_FORMATS | AUTODESK_FORMATS:
        raise CloudError("Estensione non supportata.")
    if output_format not in {"las", "laz"} or not 1 <= preview_limit <= 100_000:
        raise CloudError("Parametri di esportazione non validi.")
    with tempfile.TemporaryDirectory(prefix="cloudlab-") as directory:
        folder = Path(directory)
        source, target = folder / f"input{suffix}", folder / f"processed.{output_format}"
        source.write_bytes(data)
        has_omit = False
        if suffix == ".e57":
            schema = json.loads(run_pdal(["info", "--schema", str(source)]))
            has_omit = any(d["name"] == "Omit" for d in schema["schema"]["dimensions"])
        stages = build_pipeline(source, target, voxel, z_range, has_omit=has_omit)
        metadata = execute(stages, folder)
        preview, summary = read_preview(target, preview_limit)
        # Downloadable recipe has portable filenames, never temporary absolute paths.
        stages[0]["filename"] = f"input{suffix}"
        stages[-1]["filename"] = f"processed.{output_format}"
        return {"preview": preview, "summary": summary, "metadata": metadata,
                "pipeline": {"pipeline": stages}, "output": target.read_bytes(),
                "output_format": output_format}
