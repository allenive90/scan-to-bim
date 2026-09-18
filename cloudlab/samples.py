"""Deterministic synthetic surveys, in a local Cartesian frame measured in metres."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

import numpy as np

from cloudlab.engine import ROOT, capabilities, execute, las_writer

SCENES = {
    "campus": ("Campus architettonico", "Edifici, tetti e terreno: un piccolo rilievo urbano."),
    "terrain": ("Paesaggio collinare", "Terreno ondulato con alberi e una fascia stradale."),
    "tunnel": ("Galleria e impianti", "Sezione a volta, pavimento e tubazioni longitudinali."),
}


def make_scene(name: str, seed: int = 42) -> np.ndarray:
    count = 60_000
    rng = np.random.default_rng(seed)
    x = rng.uniform(-20, 20, count)
    y = rng.uniform(-16, 16, count)
    z = np.zeros(count)
    rgb = np.tile([95, 139, 112], (count, 1))
    classification = np.full(count, 2)
    if name == "campus":
        z = 0.15 * np.sin(x / 8) + rng.normal(0, .02, count)
        for i, (cx, cy, width, depth, height) in enumerate([(-9, -3, 10, 12, 7), (7, 4, 12, 9, 10)]):
            index = np.arange(8000 + i * 20000, 28000 + i * 20000)
            side = rng.integers(0, 5, len(index))
            xx, yy, zz = rng.uniform(-width/2, width/2, len(index)), rng.uniform(-depth/2, depth/2, len(index)), rng.uniform(0, height, len(index))
            xx[side == 0], xx[side == 1] = -width/2, width/2
            yy[side == 2], yy[side == 3] = -depth/2, depth/2
            zz[side == 4] = height + 1.4 * (1 - np.abs(xx[side == 4]) / (width/2))
            x[index], y[index], z[index] = xx + cx, yy + cy, zz
            rgb[index] = [191, 181, 160] if i == 0 else [113, 167, 184]
            rgb[index[side == 4]] = [204, 119, 83]
            classification[index] = 6
    elif name == "terrain":
        z = 2 * np.sin(x / 7) * np.cos(y / 9) + .06 * x
        road = np.abs(y - 2 * np.sin(x / 8)) < 1.8
        rgb[road] = [109, 117, 133]
        for i in range(12):
            index = np.arange(30000 + i * 2200, 32200 + i * 2200)
            cx, cy = rng.uniform(-17, 17), rng.choice([-1, 1]) * rng.uniform(6, 13)
            theta, cosphi = rng.uniform(0, 2*np.pi, len(index)), rng.uniform(-1, 1, len(index))
            radius = rng.uniform(0, 1, len(index)) ** (1/3) * 2.2
            x[index] = cx + radius * np.sqrt(1-cosphi**2) * np.cos(theta)
            y[index] = cy + radius * np.sqrt(1-cosphi**2) * np.sin(theta)
            z[index] = 2*np.sin(cx/7)*np.cos(cy/9) + .06*cx + 4 + radius*cosphi
            rgb[index] = [66, 155, 105]
            classification[index] = 5
    elif name == "tunnel":
        y = rng.uniform(-25, 25, count)
        theta = rng.uniform(0, np.pi, count)
        x, z = 5*np.cos(theta), 5*np.sin(theta)
        rgb[:] = [128, 154, 180]
        floor = np.arange(count // 4)
        x[floor], z[floor] = rng.uniform(-5, 5, len(floor)), 0
        rgb[floor] = [176, 170, 155]
        pipe = np.arange(count // 4, count // 3)
        angle = rng.uniform(0, 2*np.pi, len(pipe))
        x[pipe], z[pipe] = 3.7 + .2*np.cos(angle), 1.5 + .2*np.sin(angle)
        rgb[pipe] = [231, 163, 75]
        classification[:] = 1
    else:
        raise ValueError(f"Scena sconosciuta: {name}")
    z += rng.normal(0, .008, count)
    intensity = rng.integers(2000, 60000, count)
    return np.column_stack([x, y, z, rgb * 257, intensity, classification])


def generate(destination: Path = ROOT / "data/samples") -> dict:
    status = capabilities()
    if not status["e57"]:
        raise RuntimeError("Installare libpdal-e57: sono richiesti readers.e57 e writers.e57.")
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"seed": 42, "units": "metres", "crs": "local Cartesian; no EPSG", "synthetic": True, "scenes": []}
    for name, (title, description) in SCENES.items():
        points = make_scene(name)
        with tempfile.TemporaryDirectory(prefix="cloudlab-sample-") as temporary:
            folder = Path(temporary)
            csv = folder / "points.csv"
            np.savetxt(csv, points, delimiter=",", header="X,Y,Z,Red,Green,Blue,Intensity,Classification", comments="", fmt="%.6f")
            stages = [{"type": "readers.text", "filename": str(csv)}, las_writer(destination / f"{name}.las")]
            execute(stages, folder)
            for suffix in ["laz", "e57"]:
                target = destination / f"{name}.{suffix}"
                writer = las_writer(target) if suffix == "laz" else {"type": "writers.e57", "filename": str(target), "double_precision": True}
                execute([{"type": "readers.las", "filename": str(destination / f"{name}.las")}, writer], folder)
        manifest["scenes"].append({"id": name, "title": title, "description": description, "points": len(points), "files": [f"{name}.{s}" for s in ["las", "laz", "e57"]]})
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "data/samples")
    print(json.dumps(generate(parser.parse_args().output), indent=2))
