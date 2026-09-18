"""Bounded browser uploads for the Community Cloud demo."""
from pathlib import Path
import shutil
from uuid import uuid4

from cloudlab.engine import CloudError

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_BATCH_BYTES = 100 * 1024 * 1024
MAX_STORAGE_BYTES = 1024 * 1024 * 1024


def validate_uploads(files):
    if len(files) > 5:
        raise CloudError("La demo accetta al massimo 5 file per acquisizione.")
    if sum(f.size for f in files) > MAX_BATCH_BYTES:
        raise CloudError("La demo accetta al massimo 100 MB per acquisizione.")
    for upload in files:
        if Path(upload.name).suffix.lower() not in {".e57", ".las", ".laz"}:
            raise CloudError("Carica E57, LAS o LAZ. Per RCP/RCS esporta prima un E57 da ReCap.")
        if upload.size > MAX_UPLOAD_BYTES:
            raise CloudError("Ogni file deve essere al massimo 50 MB.")


def stage_uploads(root, files):
    validate_uploads(files)
    used = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    if used + 3 * sum(f.size for f in files) > MAX_STORAGE_BYTES:
        raise CloudError("Lo spazio disponibile per la demo è esaurito. Contatta il gestore.")
    folder = root / 'inbox' / uuid4().hex
    folder.mkdir(parents=True)
    paths = []
    try:
        for index, upload in enumerate(files):
            name = Path(upload.name.replace('\\', '/')).name
            target_dir = folder / str(index)
            target_dir.mkdir()
            target = target_dir / name
            target.write_bytes(upload.getbuffer())
            paths.append(target)
        return paths
    except Exception:
        shutil.rmtree(folder)
        raise
