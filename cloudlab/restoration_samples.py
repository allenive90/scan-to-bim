"""Synthetic restoration surveys: reproducible geometry, never diagnostic evidence.

All geometry is expressed in a local Cartesian frame in metres. The three output
formats are written and subsequently read back by the installed PDAL runtime.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

import numpy as np

from cloudlab.engine import ROOT, capabilities, execute, las_writer, process

SCENES = {
    "palazzo_storico": (
        "Palazzo storico · facciata e copertura",
        "Edificio con portale ad arco, finestre, cornici e lacune simulate dell’intonaco.",
    ),
    "chiostro": (
        "Chiostro · archi e colonne",
        "Due ali di un chiostro con colonnato, volte ad arco e pavimentazione lapidea.",
    ),
    "fontana": (
        "Fontana storica · bene lapideo",
        "Fontana a vasche sovrapposte con piedistallo, modanature e lacuna simulata del bordo.",
    ),
}
POINT_COUNT = 90_000
OUTLIER_COUNT = 45
NOISE_SIGMA_M = 0.002
LIMITATIONS = (
    "Dati interamente sintetici: geometrie, colori, lacune, alterazioni e classi non "
    "derivano da rilievi reali e non costituiscono diagnosi di degrado. Il campionamento "
    "simula superfici, non la visibilità o le stazioni di uno scanner. Nessun EPSG "
    "assegnato: coordinate cartesiane locali in metri. Le classi LAS sono etichette "
    "dimostrative; E57 può non conservare Intensity e Classification."
)


class _Cloud:
    def __init__(self, rng: np.random.Generator):
        self.rng = rng
        self.parts: list[np.ndarray] = []

    def add(self, xyz: np.ndarray, color, classification: int = 6, aged: bool = True):
        xyz = np.asarray(xyz, dtype=float)
        if not len(xyz):
            return
        rgb = np.broadcast_to(np.asarray(color, dtype=float), (len(xyz), 3)).copy()
        if aged:
            x, y, z = xyz.T
            # Deliberately synthetic patina patterns, not condition assessments.
            patina = (z < 0.85) & (np.sin(x * 2.1) + np.cos(y * 2.9) > .35)
            rgb[patina] = .58 * rgb[patina] + .42 * np.array([86, 111, 68])
            streak = (np.sin(x * 3.7 + y * 1.2) > .88) & (z < 4.3)
            rgb[streak] *= .81
        rgb += self.rng.normal(0, 4, (len(xyz), 1))
        rgb = np.clip(np.rint(rgb), 0, 255)
        xyz = xyz + self.rng.normal(0, NOISE_SIGMA_M, xyz.shape)
        intensity = np.clip(rgb.mean(axis=1) * 215 + self.rng.normal(0, 500, len(xyz)), 0, 65535)
        self.parts.append(np.column_stack([xyz, rgb * 257, intensity,
                                          np.full(len(xyz), classification)]))

    def plane(self, n, axis, value, u_range, v_range, color, classification=6, aged=True):
        uv = np.column_stack([self.rng.uniform(*u_range, n), self.rng.uniform(*v_range, n)])
        xyz = np.insert(uv, axis, value, axis=1)
        self.add(xyz, color, classification, aged)

    def cylinder(self, n, radius, z_range, center=(0, 0), color=(203, 195, 171),
                 classification=6, missing_rim=False):
        t = self.rng.uniform(0, 2 * np.pi, n)
        z = self.rng.uniform(*z_range, n)
        xyz = np.column_stack([center[0] + radius * np.cos(t), center[1] + radius * np.sin(t), z])
        if missing_rim:
            xyz = xyz[~((t > .62) & (t < .89) & (z > .96))]
        self.add(xyz, color, classification)

    def annulus(self, n, inner, outer, z, color, classification=6, missing_rim=False):
        r = np.sqrt(self.rng.uniform(inner ** 2, outer ** 2, n))
        t = self.rng.uniform(0, 2 * np.pi, n)
        xyz = np.column_stack([r * np.cos(t), r * np.sin(t), np.full(n, z)])
        if missing_rim:
            xyz = xyz[~((t > .62) & (t < .89))]
        self.add(xyz, color, classification)


def _palazzo(c: _Cloud):
    rng = c.rng
    n = 50_000
    x, z = rng.uniform(-10, 10, n), rng.uniform(0, 12, n)
    opening = (np.abs(x) < 1.45) & (z < 2.6 + np.sqrt(np.maximum(0, 1.45 ** 2 - x ** 2)))
    windows = [(cx, cz, .64, .98) for cx in (-7.4, -3.9, 3.9, 7.4) for cz in (5.4, 9)]
    windows += [(cx, 1.85, .62, .85) for cx in (-7.4, -3.9, 3.9, 7.4)]
    for cx, cz, w, h in windows:
        opening |= (abs(x - cx) < w) & (abs(z - cz) < h)
    xyz = np.column_stack([x[~opening], np.zeros((~opening).sum()), z[~opening]])
    color = np.tile([211., 190., 154.], (len(xyz), 1))
    x, z = xyz[:, 0], xyz[:, 2]
    lacuna = ((x - 5.1) / 2.3) ** 2 + ((z - 2.8) / 1.3) ** 2 < 1 + .15 * np.sin(z * 17 + x * 8)
    lacuna |= ((x + 6.2) / 1.6) ** 2 + ((z - .7) / .7) ** 2 < 1
    color[lacuna] = [153, 100, 76]
    mortar = lacuna & ((np.mod(z, .23) < .022) | (np.mod(x + (np.floor(z / .23) % 2) * .23, .46) < .018))
    color[mortar] = [202, 191, 166]
    crack = (np.abs(x + 1.9 + .12 * np.sin(z * 3) + .035 * z) < .025) & (z > 5.7) & (z < 10.7)
    color[crack] = [86, 75, 61]
    c.add(xyz, color)
    # Deep window reveals, pale surrounds, sills and wooden mullions.
    for cx, cz, w, h in windows:
        c.plane(450, 1, .28, (cx - w, cx + w), (cz - h, cz + h), [64, 77, 77], aged=False)
        for a in (-1, 1):
            c.plane(250, 1, -.09, (cx + a * w - .10, cx + a * w + .10), (cz - h - .14, cz + h + .14), [227, 218, 191])
            c.plane(220, 1, -.1, (cx - w - .15, cx + w + .15), (cz + a * h - .09, cz + a * h + .09), [225, 217, 192])
        c.plane(120, 1, .20, (cx - .045, cx + .045), (cz - h, cz + h), [110, 86, 63])
        c.plane(120, 1, .20, (cx - w, cx + w), (cz - .045, cz + .045), [110, 86, 63])
    # Portal arch in radial stone blocks, with a recessed timber door.
    theta = rng.uniform(0, np.pi, 4000)
    radius = rng.uniform(1.46, 1.80, len(theta))
    arch_color = np.tile([225., 213., 184.], (len(theta), 1))
    arch_color[np.mod(theta, np.pi / 13) < .015] *= .77
    c.add(np.column_stack([radius * np.cos(theta), np.full(len(theta), -.10), 2.6 + radius * np.sin(theta)]), arch_color)
    for cx in (-1.62, 1.62):
        c.plane(1100, 1, -.12, (cx - .17, cx + .17), (0, 2.65), [222, 212, 185])
    dx, dz = rng.uniform(-1.45, 1.45, 3000), rng.uniform(.12, 4.05, 3000)
    valid = dz < 2.6 + np.sqrt(np.maximum(0, 1.45 ** 2 - dx ** 2))
    timber = np.tile([104., 78., 56.], (valid.sum(), 1))
    timber[np.mod(dx[valid], .20) < .018] *= .68
    c.add(np.column_stack([dx[valid], np.full(valid.sum(), .35), dz[valid]]), timber, aged=False)
    # Cornices, side elevations, roof and stone forecourt give the building depth.
    for height in (.25, 3.3, 7.1, 11.5, 12):
        c.plane(1400, 1, -.18, (-10.2, 10.2), (height, height + .13), [218, 208, 179])
        c.plane(800, 2, height + .13, (-10.2, 10.2), (-.22, .05), [222, 211, 187])
    for side in (-10, 10):
        c.plane(4500, 0, side, (0, 5.4), (0, 12), [192, 176, 145])
    c.plane(4500, 1, 5.4, (-10, 10), (0, 12), [185, 173, 147])
    rx, ry = rng.uniform(-10.25, 10.25, 7000), rng.uniform(-.3, 5.7, 7000)
    rz = 12.15 + 1.1 * (1 - np.abs(ry - 2.7) / 3)
    roof = np.tile([156., 93., 68.], (len(rx), 1))
    roof[np.mod(rx, .21) < .055] += [15, 10, 6]
    c.add(np.column_stack([rx, ry, rz]), roof)
    c.plane(11000, 2, -.10, (-12, 12), (-5.3, 7), [162, 161, 146], 2)


def _chiostro(c: _Cloud):
    rng = c.rng
    # L-shaped open cloister: its courtyard remains visible in an orbiting preview.
    for axis, fixed, extent, centers in [(1, 4., (-8, 8), (-6, -2, 2, 6)),
                                         (0, -8., (-4, 4), (-2, 2))]:
        n = 23000 if axis == 1 else 13000
        u, z = rng.uniform(*extent, n), rng.uniform(.3, 5.4, n)
        opening = np.zeros(n, dtype=bool)
        for center in centers:
            d = u - center
            opening |= (abs(d) < 1.65) & (z < 2.6 + np.sqrt(np.maximum(0, 1.65 ** 2 - d ** 2)))
        xyz = np.insert(np.column_stack([u[~opening], z[~opening]]), axis, fixed, axis=1)
        c.add(xyz, [199, 189, 160])
        for center in centers:
            t, r = rng.uniform(0, np.pi, 3500), rng.uniform(1.65, 1.86, 3500)
            xyz = np.insert(np.column_stack([center + r * np.cos(t), 2.6 + r * np.sin(t)]), axis, fixed - .06, axis=1)
            color = np.tile([220., 210., 180.], (len(t), 1))
            color[np.mod(t, np.pi / 15) < .017] *= .75
            c.add(xyz, color)
        for center in np.arange(extent[0], extent[1] + .1, 4):
            xy = (center, fixed) if axis == 1 else (fixed, center)
            c.cylinder(2700, .27, (.40, 2.55), xy, [218, 210, 187])
            for level, width in ((.24, .44), (.40, .35), (2.48, .36), (2.65, .46)):
                for side in (-1, 1):
                    c.plane(230, 0, xy[0] + side * width, (xy[1] - width, xy[1] + width), (level, level + .13), [224, 216, 190])
                    c.plane(230, 1, xy[1] + side * width, (xy[0] - width, xy[0] + width), (level, level + .13), [224, 216, 190])
    c.plane(9500, 1, 6, (-10, 8), (0, 5.4), [187, 173, 143])
    c.plane(5000, 0, -10, (-4, 6), (0, 5.4), [187, 173, 143])
    for xspan, yspan in [((-10.2, 8.2), (3.6, 6.25)), ((-10.2, -7.6), (-4.2, 3.6))]:
        x, y = rng.uniform(*xspan, 5000), rng.uniform(*yspan, 5000)
        z = 5.45 + .04 * np.cos(x * 28)
        c.add(np.column_stack([x, y, z]), [156, 102, 77])
    x, y = rng.uniform(-10.5, 9, 18000), rng.uniform(-5.2, 7, 18000)
    rgb = np.tile([185., 178., 155.], (len(x), 1))
    joints = (np.mod(x, 1.15) < .03) | (np.mod(y, 1.15) < .03)
    rgb[joints] = [112, 116, 100]
    rgb[(x > -6) & (x < 6) & (y > -2.7) & (y < 2.3)] -= [14, 10, 6]
    c.add(np.column_stack([x, y, np.full(len(x), -.06)]), rgb, 2)


def _fontana(c: _Cloud):
    rng = c.rng
    stone = [210, 205, 184]
    for radius, low, high in [(3.7, 0, .15), (3.45, .15, .3), (3.1, .3, .52), (3, .52, 1.12)]:
        c.cylinder(4200, radius, (low, high), color=stone, classification=1, missing_rim=True)
        c.annulus(2100, radius - .28, radius, high, stone, 1, missing_rim=high > .96)
    c.cylinder(5500, 2.66, (.47, 1.12), color=[185, 188, 166], classification=1, missing_rim=True)
    c.annulus(11000, 0, 2.66, .47, [165, 176, 149], 1)
    c.annulus(4500, 2.66, 3, 1.12, [220, 215, 195], 1, missing_rim=True)
    for half, low, high in [(.85, .47, .8), (.7, .8, 1.08), (.47, 1.08, 1.8)]:
        for sign in (-1, 1):
            c.plane(1600, 0, sign * half, (-half, half), (low, high), stone, 1)
            c.plane(1600, 1, sign * half, (-half, half), (low, high), stone, 1)
        c.plane(1000, 2, high, (-half, half), (-half, half), stone, 1)
    c.cylinder(5000, .32, (1.4, 3.4), color=stone, classification=1)
    for radius, level, n in [(1.5, 2.05, 13000), (1.00, 3.25, 9000)]:
        t = rng.uniform(0, 2 * np.pi, n)
        r = np.sqrt(rng.uniform(.20 ** 2, radius ** 2, n))
        z = level + .40 * (r / radius) ** 1.3
        c.add(np.column_stack([r * np.cos(t), r * np.sin(t), z]), stone, 1)
        c.cylinder(4000, radius, (level + .36, level + .46), color=[221, 215, 193], classification=1)
    c.cylinder(2400, .20, (3.4, 4.05), color=stone, classification=1)
    # Small finial and four simplified carved masks under the lower bowl.
    for cx, cy, cz, ax, ay, az, n in [(0, 0, 4.14, .28, .28, .36, 3500),
                                      (0, -.58, 1.54, .21, .13, .25, 1200),
                                      (0, .58, 1.54, .21, .13, .25, 1200),
                                      (-.58, 0, 1.54, .13, .21, .25, 1200),
                                      (.58, 0, 1.54, .13, .21, .25, 1200)]:
        t, u = rng.uniform(0, 2 * np.pi, n), rng.uniform(-1, 1, n)
        s = np.sqrt(1 - u ** 2)
        c.add(np.column_stack([cx + ax * s * np.cos(t), cy + ay * s * np.sin(t), cz + az * u]), [214, 207, 182], 1)
    x, y = rng.uniform(-5, 5, 12000), rng.uniform(-5, 5, 12000)
    color = np.tile([160., 163., 151.], (len(x), 1))
    color[(np.mod(x, .6) < .025) | (np.mod(y, .6) < .025)] -= 32
    c.add(np.column_stack([x, y, np.full(len(x), -.04)]), color, 2)


def make_scene(name: str, seed: int = 42) -> np.ndarray:
    """Return exactly 90,000 XYZ/RGB16/intensity/classification rows."""
    generators = {"palazzo_storico": _palazzo, "chiostro": _chiostro, "fontana": _fontana}
    if name not in generators:
        raise ValueError(f"Scena sconosciuta: {name}")
    rng = np.random.default_rng(seed)
    cloud = _Cloud(rng)
    generators[name](cloud)
    points = np.concatenate(cloud.parts)
    count = POINT_COUNT - OUTLIER_COUNT
    if len(points) < count:
        raise RuntimeError(f"Campionamento insufficiente per {name}: {len(points)}")
    points = points[rng.choice(len(points), count, replace=False)]
    # Known synthetic isolated returns help demonstrate denoising during ingestion.
    outliers = points[rng.choice(len(points), OUTLIER_COUNT, replace=False)].copy()
    offset = rng.normal(0, 1, (OUTLIER_COUNT, 3))
    offset /= np.linalg.norm(offset, axis=1)[:, None]
    outliers[:, :3] += offset * rng.uniform(.35, 1.2, (OUTLIER_COUNT, 1))
    outliers[:, 7] = 7  # LAS low/noise; synthetic teaching label only.
    result = np.concatenate([points, outliers])
    return result[rng.permutation(len(result))]


def generate(destination: Path = ROOT / "data/restoration") -> dict:
    """Write and read back all three genuine PDAL formats, plus their manifest."""
    status = capabilities()
    if not status["e57"]:
        raise RuntimeError("Installare libpdal-e57: servono readers.e57 e writers.e57.")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 1, "name": "Patrimonio costruito · dataset dimostrativo per il restauro",
        "seed": 42, "units": "m", "crs": "local Cartesian; no EPSG", "synthetic": True,
        "scope": "Edifici storici e beni lapidei da restaurare", "limitations": LIMITATIONS,
        "noise_sigma_m": NOISE_SIGMA_M, "outliers_per_scene": OUTLIER_COUNT,
        "classification_note": "1 bene lapideo, 2 pavimentazione, 6 edificio, 7 rumore: etichette sintetiche.",
        "scenes": [],
    }
    for name, (title, description) in SCENES.items():
        points = make_scene(name)
        with tempfile.TemporaryDirectory(prefix="cloudlab-restoration-") as temporary:
            folder = Path(temporary)
            csv = folder / "points.csv"
            np.savetxt(csv, points, delimiter=",", header="X,Y,Z,Red,Green,Blue,Intensity,Classification", comments="", fmt="%.6f")
            las = destination / f"{name}.las"
            execute([{"type": "readers.text", "filename": str(csv)}, las_writer(las)], folder)
            for suffix in ("laz", "e57"):
                target = destination / f"{name}.{suffix}"
                writer = las_writer(target) if suffix == "laz" else {"type": "writers.e57", "filename": str(target), "double_precision": True}
                execute([{"type": "readers.las", "filename": str(las)}, writer], folder)
            checked = {}
            for suffix in ("las", "laz", "e57"):
                path = destination / f"{name}.{suffix}"
                result = process(path.read_bytes(), f".{suffix}", preview_limit=256)
                summary = result["summary"]
                if summary["points"] != POINT_COUNT or not np.isfinite(summary["minimum"] + summary["maximum"]).all():
                    raise RuntimeError(f"Verifica PDAL fallita: {path.name}")
                if np.max(result["preview"]["Red"]) <= 0:
                    raise RuntimeError(f"Colori RGB non conservati: {path.name}")
                checked[suffix] = {"points": summary["points"], "bytes": path.stat().st_size, "reader_verified": True}
        manifest["scenes"].append({
            "id": name, "title": title, "description": description,
            "restoration_scope": "Restauro architettonico" if name != "fontana" else "Conservazione del patrimonio lapideo",
            "points": len(points), "synthetic": True, "units": "m", "crs": "local Cartesian; no EPSG",
            "minimum": points[:, :3].min(axis=0).round(3).tolist(),
            "maximum": points[:, :3].max(axis=0).round(3).tolist(),
            "outliers": OUTLIER_COUNT, "limitations": LIMITATIONS,
            "files": [f"{name}.{s}" for s in ("las", "laz", "e57")], "validation": checked,
        })
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/restoration")
    print(json.dumps(generate(parser.parse_args().output), indent=2, ensure_ascii=False))
