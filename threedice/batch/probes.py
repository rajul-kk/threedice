"""Probes in physical coordinates (um) and their mapping onto the cell grid.

A mapping is stored as (P, 4) flat indices into a (ny * nx) field and (P, 4) weights, so that
    T_probe = sum(field.ravel()[index] * weight, axis=1)
for both modes. "cell" uses one weight of 1 (the other three repeat the index with weight 0).
"""
import numpy as np

from .grid import Grid

MODES = ("cell", "bilinear")


class Probes:
    """Probe points: `element` (stack element whose Tmap is read), x along the chip length, y along the width, in um."""

    def __init__(self, element, x, y, names=None):
        x = np.atleast_1d(np.asarray(x, dtype=float))
        y = np.atleast_1d(np.asarray(y, dtype=float))
        if isinstance(element, str):
            element = [element] * x.size
        element = [str(e) for e in element]
        if not (x.ndim == y.ndim == 1 and x.size == y.size == len(element)):
            raise ValueError("element, x and y must have the same length")
        if names is None:
            names = [f"p{i}" for i in range(x.size)]
        names = [str(n) for n in names]
        if len(names) != x.size or len(set(names)) != len(names):
            raise ValueError("probe names must be unique and one per probe")
        self.element, self.x, self.y, self.names = element, x, y, names

    def __len__(self):
        return self.x.size

    @property
    def xy(self):
        return np.stack([self.x, self.y], axis=1)

    @property
    def elements(self):
        """Distinct elements, in first-seen order."""
        return list(dict.fromkeys(self.element))


def map_probes(grid: Grid, x, y, mode="cell"):
    """Return (index (P, 4) int64, weight (P, 4) float64, cell (P, 2) int64 as (row, col))."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
    row, col = grid.cell_of(x, y)
    x, y = np.asarray(x, float), np.asarray(y, float)
    cell = np.stack([row, col], axis=1)
    p = row.size
    if mode == "cell":
        flat = row * grid.nx + col
        index = np.repeat(flat[:, None], 4, axis=1)
        weight = np.zeros((p, 4))
        weight[:, 0] = 1.0
        return index, weight, cell

    def axis(v, d, n):
        if n == 1:
            z = np.zeros(v.shape, np.int64)
            return z, z, np.zeros(v.shape)
        f = v / d - 0.5                                   # position in units of cells, measured from centre 0
        i0 = np.clip(np.floor(f).astype(np.int64), 0, n - 2)
        t = np.clip(f - i0, 0.0, 1.0)                     # clamped: constant in the half-cell band at the chip edge
        return i0, i0 + 1, t

    c0, c1, tx = axis(x, grid.dx, grid.nx)
    r0, r1, ty = axis(y, grid.dy, grid.ny)
    index = np.stack([r0 * grid.nx + c0, r0 * grid.nx + c1, r1 * grid.nx + c0, r1 * grid.nx + c1], axis=1)
    weight = np.stack([(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty], axis=1)
    return index, weight, cell


def probe_values(field, index, weight):
    """Probe values from one (ny, nx) field, or a stack (..., ny, nx) of fields -> (..., P)."""
    field = np.asarray(field, dtype=np.float64)
    flat = field.reshape(field.shape[:-2] + (-1,))
    return np.sum(flat[..., index] * weight, axis=-1)
