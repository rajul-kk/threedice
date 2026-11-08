"""Readers for 3D-ICE text outputs: Tmap (`final`) and the xaxis/yaxis files."""
import numpy as np


class TmapError(ValueError):
    pass


def read_tmap(path, shape=None):
    """A `Tmap (..., final)` file -> (rows, cols) float64. Lines starting with '%' are headers. Rows run along the chip
    width (y), columns along the length (x), row 0 at y = 0."""
    rows = []
    with open(path, "r", encoding="latin-1") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("%"):
                continue
            rows.append([float(t) for t in s.split()])
    if not rows:
        raise TmapError(f"{path}: no temperature rows")
    widths = {len(r) for r in rows}
    if len(widths) != 1:
        raise TmapError(f"{path}: rows of different lengths {sorted(widths)}")
    a = np.array(rows, dtype=np.float64)
    if shape is not None and a.shape != tuple(shape):
        raise TmapError(f"{path}: Tmap is {a.shape}, the grid is {tuple(shape)} (rows = width cells, cols = length cells)")
    if not np.all(np.isfinite(a)):
        raise TmapError(f"{path}: non-finite temperatures")
    return a


def read_axis(path):
    with open(path, "r", encoding="latin-1") as f:
        return np.array([float(t) for t in f.read().split()], dtype=np.float64)
