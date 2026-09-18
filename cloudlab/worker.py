"""Single local ingestion worker; persisted jobs survive UI reruns and restarts."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

import numpy as np
from pyproj import CRS

from cloudlab.catalog import Catalog, now, units_match
from cloudlab.engine import AUTODESK_FORMATS, CloudError, pdal_binary, reader

PREVIEW_LIMIT = 30_000


def command(args, timeout=7200):
    try:
        result = subprocess.run([pdal_binary(), *map(str, args)], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise CloudError("Tempo massimo PDAL superato (2 ore). Suddividi il lotto o verifica il file.") from exc
    if result.returncode:
        raise CloudError((result.stderr or result.stdout)[-4000:])
    return json.loads(result.stdout) if result.stdout.strip().startswith("{") else result.stdout


class Worker:
    def __init__(self, catalog):
        self.catalog = catalog
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.run, name="scan-to-bim-ingestion", daemon=True)
            self.thread.start()
        return self

    def progress(self, job, phase, progress):
        with self.catalog.connect() as db:
            db.execute("UPDATE jobs SET phase=?, progress=?, updated=? WHERE id=?", (phase, progress, now(), job["id"]))

    def recover(self):
        # Called only while holding the exclusive OS worker lock.
        with self.catalog.connect() as db:
            for row in db.execute("SELECT asset_id FROM jobs WHERE status='RUNNING'").fetchall():
                db.execute("UPDATE assets SET status='QUEUED',updated=? WHERE id=?", (now(), row["asset_id"]))
                self.catalog.event(db, row["asset_id"], "RECOVERED", "Ripresa dopo interruzione del worker")
            db.execute("UPDATE jobs SET status='QUEUED',phase='Ripresa dopo interruzione',progress=0 WHERE status='RUNNING'")
        from cloudlab.pdal_workbench import recover_runs
        recover_runs(self.catalog)

    def run(self):
        with (self.catalog.root / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return  # Another Streamlit process or CLI worker owns this catalogue.
            self.recover()
            while not self.stop_event.is_set():
                try:
                    if not self.once():
                        self.stop_event.wait(1)
                except Exception:
                    # A temporary DB/filesystem error must not permanently kill the queue.
                    self.stop_event.wait(3)

    def once(self):
        with self.catalog.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status='QUEUED' ORDER BY created,rowid LIMIT 1").fetchone()
            if not row:
                db.commit()
                from cloudlab.pdal_workbench import process_next
                return process_next(self.catalog)
            job = dict(row)
            db.execute("UPDATE jobs SET status='RUNNING', attempts=attempts+1, updated=? WHERE id=?", (now(), job["id"]))
            db.execute("UPDATE assets SET status='RUNNING',updated=? WHERE id=?", (now(), job["asset_id"]))
            self.catalog.event(db, job["asset_id"], "STARTED", "Copia, checksum e verifica PDAL")
        try:
            self.ingest(job)
        except Exception as exc:
            with self.catalog.connect() as db:
                db.execute("UPDATE assets SET status='FAILED',error=?,updated=? WHERE id=?", (str(exc)[-4000:], now(), job["asset_id"]))
                db.execute("UPDATE jobs SET status='FAILED',phase='Errore',updated=? WHERE id=?", (now(), job["id"]))
                self.catalog.event(db, job["asset_id"], "FAILED", str(exc)[-4000:])
        return True

    def archive(self, job, asset):
        source = Path(asset["source"])
        before = source.stat()
        if not before.st_size:
            raise CloudError("File vuoto.")
        if shutil.disk_usage(self.catalog.root).free < before.st_size + 64 * 1024**2:
            raise CloudError("Spazio insufficiente nell’archivio per conservare l’originale.")
        partial = self.catalog.root / "work" / f'{job["id"]}.part'
        digest = hashlib.sha256()
        copied, last_update = 0, 0
        try:
            with source.open("rb") as src, partial.open("wb") as dst:
                while chunk := src.read(8 * 1024**2):
                    digest.update(chunk)
                    dst.write(chunk)
                    copied += len(chunk)
                    if time.monotonic() - last_update > .5:
                        self.progress(job, "Archiviazione e checksum SHA-256", min(45, copied / before.st_size * 45))
                        last_update = time.monotonic()
                dst.flush()
                os.fsync(dst.fileno())
            after = source.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or copied != before.st_size:
                raise CloudError("Il sorgente è cambiato durante la copia. Attendi il completamento del trasferimento e riprova.")
            checksum = digest.hexdigest()
            raw = self.catalog.root / "originals" / f'{checksum}{asset["suffix"]}'
            if raw.exists():
                # Verify an existing content-addressed object before reusing it.
                with raw.open("rb") as stream:
                    existing = hashlib.file_digest(stream, "sha256").hexdigest()
                if existing != checksum:
                    raise CloudError("Checksum incoerente nell’archivio: ripristinare l’originale conservato.")
                partial.unlink()
            else:
                partial.replace(raw)
                raw.chmod(0o444)
            with self.catalog.connect() as db:
                db.execute("UPDATE assets SET sha256=?,raw_path=?,size=? WHERE id=?", (checksum, str(raw), copied, asset["id"]))
            # Browser uploads are staging files owned by this application.
            if source.is_relative_to(self.catalog.root / "inbox"):
                source.unlink()
                with self.catalog.connect() as db:
                    db.execute("UPDATE assets SET source=? WHERE id=?", (str(raw), asset["id"]))
            return raw, checksum
        finally:
            partial.unlink(missing_ok=True)

    def finish(self, job, state, metadata, preview=None, duplicate=None):
        with self.catalog.connect() as db:
            db.execute("""UPDATE assets SET status=?,metadata=?,preview_path=?,duplicate_of=?,error='',updated=? WHERE id=?""",
                       (state, json.dumps(metadata), str(preview) if preview else None, duplicate, now(), job["asset_id"]))
            db.execute("UPDATE jobs SET status='DONE',phase='Completato',progress=100,updated=? WHERE id=?", (now(), job["id"]))
            self.catalog.event(db, job["asset_id"], state, "; ".join(metadata.get("warnings", [])) or "Verifica completata")

    def ingest(self, job):
        asset = self.catalog.asset(job["asset_id"])
        raw, checksum = self.archive(job, asset)
        duplicate = self.catalog.rows("""SELECT id FROM assets WHERE project_id=? AND sha256=? AND id!=?
            AND status IN ('READY','REVIEW','CONVERSION') ORDER BY created LIMIT 1""", (asset["project_id"], checksum, asset["id"]))
        if duplicate:
            self.finish(job, "DUPLICATE", {"warnings": ["Contenuto identico a un originale già registrato nel progetto."]}, duplicate=duplicate[0]["id"])
            return
        if asset["suffix"] in AUTODESK_FORMATS:
            self.finish(job, "CONVERSION", {"warnings": ["Originale archiviato. Esportare in E57 con ReCap Pro e associare il derivato; il contenuto RCP/RCS non è validato da PDAL.",
                                                                       "Per RCP conservare tutti i file RCS e le dipendenze del progetto."]})
            return
        self.progress(job, "Lettura intestazione e schema PDAL", 50)
        info = command(["info", "--summary", raw])
        header = command(["info", "--schema", "--metadata", raw])
        summary = info.get("summary", {})
        dimension_names = [d["name"] for d in header["schema"]["dimensions"]]
        if not {"X", "Y", "Z"}.issubset(dimension_names):
            raise CloudError("Coordinate XYZ mancanti nello schema.")
        points = int(summary.get("num_points", 0))
        if points <= 0:
            raise CloudError("Nessun punto dichiarato nel rilievo.")
        folder = self.catalog.root / "work" / asset["id"] / f'attempt-{job["attempts"] + 1}'
        folder.mkdir(parents=True, exist_ok=True)
        dims = [d for d in ("X", "Y", "Z", "Red", "Green", "Blue", "Intensity", "Classification") if d in dimension_names]
        stages = [reader(raw), {"type": "filters.stats", "dimensions": "X,Y,Z" + (",Omit" if "Omit" in dimension_names else "")}]
        if "Omit" in dimension_names:
            stages.append({"type": "filters.expression", "expression": "Omit == 0"})
        stages += [{"type": "filters.decimation", "step": max(1, math.ceil(points / PREVIEW_LIMIT))},
                   {"type": "filters.head", "count": PREVIEW_LIMIT},
                   {"type": "writers.text", "filename": str(folder / "preview.csv"), "order": ",".join(dims),
                    "keep_unspecified": False, "precision": 8}]
        pipeline_path, metadata_path = folder / "validation.json", folder / "pdal-metadata.json"
        pipeline_path.write_text(json.dumps({"pipeline": stages}, indent=2))
        self.progress(job, "Verifica completa PDAL in streaming", 60)
        command(["pipeline", pipeline_path, "--stream", "--metadata", metadata_path])
        metadata = json.loads(metadata_path.read_text())
        stats = metadata["stages"]["filters.stats"]["statistic"]
        xyz = {s["name"]: s for s in stats if s["name"] in {"X", "Y", "Z"}}
        read_count = int(xyz["X"]["count"])
        if read_count != points:
            raise CloudError(f"Conteggio incoerente: intestazione {points:,}, lettura completa {read_count:,}.")
        if not all(math.isfinite(float(s[k])) for s in xyz.values() for k in ("minimum", "maximum", "average")):
            raise CloudError("Coordinate non finite: il rilievo richiede una verifica della sorgente.")
        self.progress(job, "Creazione anteprima e controllo del riferimento", 90)
        csv = np.genfromtxt(folder / "preview.csv", delimiter=",", names=True, dtype=float)
        csv = np.atleast_1d(csv)
        preview = {key: csv[key] if key in csv.dtype.names else np.zeros(len(csv))
                   for key in ("X", "Y", "Z", "Red", "Green", "Blue", "Intensity", "Classification")}
        if not len(csv):
            raise CloudError("Nessun punto valido per l’anteprima.")
        preview_path = folder / "preview.npz"
        np.savez_compressed(preview_path, **preview)
        file_meta = header.get("metadata", {})
        wkt = file_meta.get("spatialreference") or summary.get("srs", {}).get("wkt", "")
        crs = CRS.from_user_input(wkt) if wkt else None
        warnings = []
        if not crs:
            warnings.append("CRS assente nel file: serve una dichiarazione del riferimento e delle unità.")
        if asset["suffix"] == ".e57":
            warnings.append("Originale E57 conservato integralmente; l’anteprima appiattisce le scansioni e non include immagini.")
        context = asset["context"]
        conflict = bool(crs and (not context["project_crs"] or not crs.equals(CRS.from_user_input(context["project_crs"]))
                                or not units_match(context["project_units"], crs.axis_info[0].unit_name)))
        if conflict:
            warnings.append("Il CRS del file differisce dal riferimento del progetto: verificare l’allineamento nella fase successiva.")
        omit = next((s for s in stats if s["name"] == "Omit"), None)
        if omit and omit["maximum"] > 0:
            warnings.append("Presenti punti marcati Omit: conservati nell’originale, esclusi dall’anteprima.")
        ready = bool(context.get("reference_confirmed")) and not conflict
        result = {"points": read_count, "bounds": {k: [xyz[k]["minimum"], xyz[k]["maximum"]] for k in ("X", "Y", "Z")},
                  "dimensions": dimension_names, "file_crs": crs.to_string() if crs else None,
                  "file_units": crs.axis_info[0].unit_name if crs and crs.axis_info else None,
                  "warnings": warnings, "preview_points": len(csv), "validation": "full_streaming_read",
                  "pdal_version": info.get("pdal_version"), "header": header,
                  "archive_mtime_ns": raw.stat().st_mtime_ns,
                  "validation_pipeline": str(pipeline_path), "validation_metadata": str(metadata_path)}
        if ready:
            result["declaration"] = {"reference": context["project_reference"], "crs": context["project_crs"],
                                     "units": context["project_units"], "confirmed_at": now(), "note": "Dichiarazione registrata all’ingresso del lotto"}
        self.finish(job, "READY" if ready else "REVIEW", result, preview_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage", type=Path)
    parser.add_argument("--once", action="store_true", help="Elabora la coda fino a esaurimento")
    args = parser.parse_args()
    worker = Worker(Catalog(args.storage))
    if args.once:
        with (worker.catalog.root / "worker.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            worker.recover()
            while worker.once():
                pass
    else:
        worker.run()
