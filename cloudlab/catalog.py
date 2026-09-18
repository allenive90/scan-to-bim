"""Persistent ingestion catalogue. SQLite owns job state; files own survey bytes."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid

from pyproj import CRS

from cloudlab.engine import ROOT, DIRECT_FORMATS, AUTODESK_FORMATS, CloudError

FORMATS = DIRECT_FORMATS | AUTODESK_FORMATS
LABELS = {"QUEUED": "In coda", "RUNNING": "In verifica", "READY": "Pronto",
          "REVIEW": "Da completare", "CONVERSION": "Conversione richiesta",
          "DUPLICATE": "Duplicato", "FAILED": "Errore"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uid():
    return uuid.uuid4().hex


class Catalog:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or os.environ.get("CLOUDLAB_STORAGE", ROOT / "storage")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("originals", "inbox", "work", "deliveries"):
            (self.root / name).mkdir(exist_ok=True)
        self.db = self.root / "catalog.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, reference TEXT NOT NULL,
                    crs TEXT NOT NULL, units TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    name TEXT NOT NULL, context TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    batch_id TEXT NOT NULL REFERENCES batches(id), name TEXT NOT NULL,
                    source TEXT NOT NULL, suffix TEXT NOT NULL, size INTEGER NOT NULL,
                    status TEXT NOT NULL, sha256 TEXT, raw_path TEXT, duplicate_of TEXT,
                    parent_id TEXT REFERENCES assets(id), metadata TEXT NOT NULL DEFAULT '{}',
                    preview_path TEXT, error TEXT NOT NULL DEFAULT '',
                    created TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS assets_project ON assets(project_id, created);
                CREATE INDEX IF NOT EXISTS assets_hash ON assets(project_id, sha256);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
                    status TEXT NOT NULL, phase TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
                    action TEXT NOT NULL, detail TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    path TEXT NOT NULL, count INTEGER NOT NULL, created TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self, query, params=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, params)]

    def projects(self):
        return self.rows("SELECT * FROM projects ORDER BY created, name")

    def project(self, project_id):
        result = self.rows("SELECT * FROM projects WHERE id=?", (project_id,))
        if not result:
            raise CloudError("Progetto inesistente.")
        return result[0]

    def create_project(self, name, reference, crs="", units="m"):
        if not name.strip() or not reference.strip() or units not in {"m", "mm", "ft", "degree"}:
            raise CloudError("Indica nome, riferimento spaziale e unità del progetto.")
        if crs.strip():
            try:
                parsed = CRS.from_user_input(crs)
                if parsed.axis_info and not units_match(units, parsed.axis_info[0].unit_name):
                    raise CloudError("Le unità dichiarate non corrispondono a quelle del CRS.")
                crs = parsed.to_string()
            except CloudError:
                raise
            except Exception as exc:
                raise CloudError("CRS non valido: usa per esempio EPSG:32632.") from exc
        project_id = uid()
        with self.connect() as db:
            db.execute("INSERT INTO projects VALUES (?,?,?,?,?,?)", (project_id, name.strip(), reference.strip(), crs.strip(), units, now()))
        return project_id

    def enqueue(self, project_id, paths, batch_name, context=None, parent_id=None):
        project = self.project(project_id)
        paths = [Path(path).expanduser().resolve() for path in paths]
        if not paths:
            raise CloudError("Seleziona almeno un file.")
        if len(paths) > 5000:
            raise CloudError("Massimo 5.000 file per lotto; suddividi il dataset in più lotti.")
        for path in paths:
            if path.suffix.lower() not in FORMATS or not path.is_file():
                raise CloudError(f"File non disponibile o formato non ammesso: {path.name}")
            if path.is_relative_to(self.root) and not path.is_relative_to(self.root / "inbox"):
                raise CloudError("Seleziona una cartella sorgente esterna all’archivio di ingestion.")
        if parent_id:
            parent = self.asset(parent_id)
            if parent["project_id"] != project_id or parent["suffix"] not in AUTODESK_FORMATS:
                raise CloudError("L’originale da associare deve essere un RCP/RCS dello stesso progetto.")
            if any(path.suffix.lower() != ".e57" for path in paths):
                raise CloudError("Associa al file Autodesk un’esportazione E57.")
        context = {**(context or {}), "project_reference": project["reference"],
                   "project_crs": project["crs"], "project_units": project["units"]}
        batch_id, timestamp = uid(), now()
        with self.connect() as db:
            db.execute("INSERT INTO batches VALUES (?,?,?,?,?)",
                       (batch_id, project_id, batch_name.strip() or "Nuovo lotto", json.dumps(context), timestamp))
            for path in paths:
                asset_id = uid()
                db.execute("""INSERT INTO assets (id,project_id,batch_id,name,source,suffix,size,status,parent_id,created,updated)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (asset_id, project_id, batch_id, path.name, str(path), path.suffix.lower(), path.stat().st_size,
                     "QUEUED", parent_id, timestamp, timestamp))
                db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", (uid(), asset_id, "QUEUED", "In attesa", 0, 0, timestamp, timestamp))
                self.event(db, asset_id, "RECEIVED", f"Lotto {batch_name}; origine {path}")
        return batch_id

    @staticmethod
    def event(db, asset_id, action, detail):
        db.execute("INSERT INTO events(asset_id,action,detail,created) VALUES (?,?,?,?)", (asset_id, action, detail, now()))

    def asset(self, asset_id):
        rows = self.rows("""SELECT a.*, b.name batch_name, b.context FROM assets a
            JOIN batches b ON b.id=a.batch_id WHERE a.id=?""", (asset_id,))
        if not rows:
            raise CloudError("Rilievo non trovato.")
        row = rows[0]
        row["metadata"] = json.loads(row["metadata"])
        row["context"] = json.loads(row["context"])
        return row

    def assets(self, project_id, search="", state="", limit=100, offset=0, batch_id=""):
        return self.rows("""SELECT a.*, b.name batch_name FROM assets a JOIN batches b ON b.id=a.batch_id
            WHERE a.project_id=? AND (?='' OR a.status=?) AND a.name LIKE ? AND (?='' OR a.batch_id=?)
            ORDER BY a.created DESC, a.rowid DESC LIMIT ? OFFSET ?""",
            (project_id, state, state, f"%{search}%", batch_id, batch_id, limit, offset))

    def counts(self, project_id):
        return self.rows("SELECT status, COUNT(*) count, SUM(size) bytes FROM assets WHERE project_id=? GROUP BY status", (project_id,))

    def retry(self, asset_id):
        with self.connect() as db:
            if not db.execute("UPDATE assets SET status='QUEUED',error='',updated=? WHERE id=? AND status='FAILED'", (now(), asset_id)).rowcount:
                raise CloudError("Si possono riprovare solo i rilievi in errore.")
            db.execute("UPDATE jobs SET status='QUEUED',phase='Nuovo tentativo',progress=0,updated=? WHERE asset_id=?", (now(), asset_id))
            self.event(db, asset_id, "RETRY", "Nuovo tentativo richiesto")

    def confirm_reference(self, asset_id, reference, units, note):
        if not reference.strip() or units not in {"m", "mm", "ft", "degree"} or not note.strip():
            raise CloudError("Compila riferimento, unità e nota di verifica.")
        with self.connect() as db:
            row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
            if not row or row["status"] != "REVIEW":
                raise CloudError("Il rilievo non è in attesa di completamento.")
            meta = json.loads(row["metadata"])
            if meta.get("file_crs"):
                try:
                    same_crs = CRS.from_user_input(reference).equals(CRS.from_user_input(meta["file_crs"]))
                except Exception:
                    same_crs = False
                if not same_crs or not units_match(units, meta.get("file_units", "")):
                    raise CloudError("La dichiarazione deve rispettare CRS e unità rilevati nel file. Per cambiarli serve una trasformazione nel preprocessing.")
            meta["declaration"] = {"reference": reference.strip(), "units": units, "note": note.strip(), "confirmed_at": now()}
            db.execute("UPDATE assets SET status='READY',metadata=?,updated=? WHERE id=?", (json.dumps(meta), now(), asset_id))
            self.event(db, asset_id, "REFERENCE_CONFIRMED", json.dumps(meta["declaration"]))

    def delivery(self, project_id, asset_ids):
        if not asset_ids:
            raise CloudError("Seleziona almeno un rilievo pronto.")
        assets = [self.asset(a) for a in dict.fromkeys(asset_ids)]
        if any(a["project_id"] != project_id or a["status"] != "READY" for a in assets):
            raise CloudError("La consegna accetta solo rilievi pronti dello stesso progetto.")
        if any(not Path(a["raw_path"]).is_file() for a in assets):
            raise CloudError("Un originale archiviato non è disponibile: ripristinalo prima della consegna.")
        if any(Path(a["raw_path"]).stat().st_size != a["size"] or
               Path(a["raw_path"]).stat().st_mtime_ns != a["metadata"]["archive_mtime_ns"] for a in assets):
            raise CloudError("Un originale è cambiato dopo la verifica: ripristinalo e ripeti l’ingestion.")
        delivery_id = uid()
        manifest = {"schema_version": "scan-to-bim.ingestion/1", "id": delivery_id, "created": now(),
                    "stage": "ingestion", "next_stage": "pdal_preprocessing", "project": self.project(project_id), "assets": []}
        for asset in assets:
            manifest["assets"].append({k: asset[k] for k in ("id", "batch_id", "batch_name", "name", "sha256", "raw_path", "source", "size", "parent_id", "metadata", "context")})
        path = self.root / "deliveries" / f"{delivery_id}.json"
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        with self.connect() as db:
            db.execute("INSERT INTO deliveries VALUES (?,?,?,?,?)", (delivery_id, project_id, str(path), len(assets), now()))
            for asset in assets:
                self.event(db, asset["id"], "HANDOFF", f"Manifest {delivery_id}; preprocessing non ancora eseguito")
        return path


def discover(folder, recursive=True, excluded=None):
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise CloudError("La cartella deve essere accessibile dal computer che esegue Streamlit.")
    if excluded and folder.is_relative_to(Path(excluded).resolve()):
        raise CloudError("Seleziona una cartella esterna all’archivio di ingestion.")
    found = []
    # os.walk does not follow directory symlinks. Prune the managed storage.
    for current, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and
                         not (excluded and (Path(current) / d).resolve().is_relative_to(Path(excluded).resolve())))
        for name in sorted(files):
            path = Path(current) / name
            if path.suffix.lower() in FORMATS and not path.is_symlink():
                found.append(path)
                if len(found) > 5000:
                    raise CloudError("Oltre 5.000 file: scegli una sottocartella per questo lotto.")
        if not recursive:
            break
    return found


def units_match(declared, detected):
    text = (detected or "").lower()
    return ((declared == "m" and text in {"metre", "meter"}) or
            (declared == "mm" and text in {"millimetre", "millimeter"}) or
            (declared == "ft" and ("foot" in text or "feet" in text)) or
            (declared == "degree" and "degree" in text))
