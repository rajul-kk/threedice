"""Uniform in-plane cell grid of a 3D-ICE stack (lengths in micrometres).

x runs along the chip *length* (Tmap columns), y along the chip *width* (Tmap rows); row 0 is y = 0.
"""
import math

import numpy as np

ALIGN_TOL = 1e-6          # in units of one cell


class GridError(ValueError):
    pass


class Grid:
    """Chip of size (length, width) split into cells of size (dx, dy); nx columns, ny rows."""

    def __init__(self, length, width, dx, dy):
        length, width, dx, dy = (float(v) for v in (length, width, dx, dy))
        if min(length, width, dx, dy) <= 0:
            raise GridError("chip and cell sizes must be positive")
        nx, ny = length / dx, width / dy
        if abs(nx - round(nx)) > ALIGN_TOL or abs(ny - round(ny)) > ALIGN_TOL:
            # 3D-ICE truncates silently (chip length 1100, cell 250 -> 4 columns, the last 100 um dropped)
            raise GridError(f"chip {length} x {width} um is not a whole number of {dx} x {dy} um cells; "
                            "3D-ICE would silently drop the remainder")
        self.length, self.width, self.dx, self.dy = length, width, dx, dy
        self.nx, self.ny = int(round(nx)), int(round(ny))

    def __eq__(self, other):
        return isinstance(other, Grid) and (self.length, self.width, self.dx, self.dy) == \
            (other.length, other.width, other.dx, other.dy)

    def __repr__(self):
        return f"Grid(length={self.length}, width={self.width}, dx={self.dx}, dy={self.dy})"

    @property
    def shape(self):
        """(ny, nx): the shape of a Tmap of this grid."""
        return (self.ny, self.nx)

    @property
    def x_centers(self):
        return (np.arange(self.nx) + 0.5) * self.dx

    @property
    def y_centers(self):
        return (np.arange(self.ny) + 0.5) * self.dy

    @property
    def x_edges(self):
        return np.arange(self.nx + 1) * self.dx

    @property
    def y_edges(self):
        return np.arange(self.ny + 1) * self.dy

    def check_inside(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        bad = (x < 0) | (x > self.length) | (y < 0) | (y > self.width) | ~np.isfinite(x) | ~np.isfinite(y)
        if np.any(bad):
            i = int(np.flatnonzero(bad)[0])
            raise GridError(f"point {i} ({float(x.flat[i])}, {float(y.flat[i])}) um is outside the chip "
                            f"[0, {self.length}] x [0, {self.width}]")
        return x, y

    def cell_of(self, x, y):
        """(row, col) of the cell containing each point. A point on a shared boundary goes to the higher-index cell;
        a point on the far chip edge goes to the last cell."""
        x, y = self.check_inside(x, y)
        col = np.minimum(np.floor(x / self.dx + ALIGN_TOL).astype(np.int64), self.nx - 1)
        row = np.minimum(np.floor(y / self.dy + ALIGN_TOL).astype(np.int64), self.ny - 1)
        return row, col

    def on_x_line(self, x):
        """True where x lies on a cell boundary along the length (within ALIGN_TOL of a cell)."""
        f = np.asarray(x, float) / self.dx
        return np.abs(f - np.round(f)) <= ALIGN_TOL

    def on_y_line(self, y):
        f = np.asarray(y, float) / self.dy
        return np.abs(f - np.round(f)) <= ALIGN_TOL

    def snap_x(self, x):
        return float(np.round(float(x) / self.dx) * self.dx)

    def snap_y(self, y):
        return float(np.round(float(y) / self.dy) * self.dy)

    def check_axes(self, x_centers, y_centers, tol=0.006):
        """Compare the axes printed by 3D-ICE (two decimals) with this grid."""
        x_centers, y_centers = np.asarray(x_centers, float), np.asarray(y_centers, float)
        if x_centers.shape != (self.nx,) or y_centers.shape != (self.ny,):
            raise GridError(f"3D-ICE axes have {x_centers.size} x {y_centers.size} cells, the stack gives "
                            f"{self.nx} x {self.ny}")
        err = max(float(np.max(np.abs(x_centers - self.x_centers))), float(np.max(np.abs(y_centers - self.y_centers))))
        if not math.isfinite(err) or err > tol:
            raise GridError(f"3D-ICE cell centres differ from the stack's uniform grid by {err} um")
