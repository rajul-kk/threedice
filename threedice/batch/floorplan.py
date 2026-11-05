"""Floorplans generated per case: blocks, rendering to 3D-ICE .flp text, grid alignment (die-edge partial cells)."""
import re

import numpy as np

from .grid import Grid

EDGE_POLICIES = ("error", "snap", "allow")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class FloorplanError(ValueError):
    pass


class Block:
    """Rectangle at (x, y) (lower-left corner, um) of size (length along x, width along y) dissipating `power` W."""

    __slots__ = ("name", "x", "y", "length", "width", "power")

    def __init__(self, name, x, y, length, width, power):
        if not _NAME.match(str(name)):
            raise FloorplanError(f"block name {name!r} is not a 3D-ICE identifier")
        self.name, self.x, self.y = str(name), float(x), float(y)
        self.length, self.width, self.power = float(length), float(width), float(power)
        if self.length <= 0 or self.width <= 0:
            raise FloorplanError(f"block {self.name} has a non-positive size")
        if not np.isfinite(self.power):
            raise FloorplanError(f"block {self.name} has a non-finite power")

    def shifted(self, dx, dy):
        return Block(self.name, self.x + dx, self.y + dy, self.length, self.width, self.power)

    def __repr__(self):
        return f"Block({self.name!r}, {self.x}, {self.y}, {self.length}, {self.width}, {self.power})"


class Floorplan:
    def __init__(self, blocks):
        self.blocks = list(blocks)
        if not self.blocks:
            raise FloorplanError("a floorplan needs at least one block")
        names = [b.name for b in self.blocks]
        if len(set(names)) != len(names):
            raise FloorplanError("block names must be unique within a floorplan")

    @classmethod
    def from_power_grid(cls, power, grid: Grid, origin=(0.0, 0.0), prefix="c"):
        """One block per non-zero cell of an (rows, cols) array of watts per cell, on `grid`'s cell size, with the
        array's cell (0, 0) at `origin` (um). An all-zero array gives one zero-power block."""
        power = np.asarray(power, dtype=float)
        if power.ndim != 2:
            raise FloorplanError("a power grid must be 2-D (rows along y, columns along x)")
        ox, oy = float(origin[0]), float(origin[1])
        blocks = []
        for r, c in zip(*np.nonzero(power)):
            blocks.append(Block(f"{prefix}{r}_{c}", ox + c * grid.dx, oy + r * grid.dy, grid.dx, grid.dy, power[r, c]))
        if not blocks:
            blocks.append(Block(f"{prefix}0_0", ox, oy, grid.dx, grid.dy, 0.0))
        return cls(blocks)

    def shifted(self, dx, dy):
        return Floorplan([b.shifted(dx, dy) for b in self.blocks])

    @property
    def total_power(self):
        return sum(b.power for b in self.blocks)

    def render(self):
        """3D-ICE floorplan text (one constant power value per block)."""
        out = []
        for b in self.blocks:
            out.append(f"{b.name} :\n  position {b.x!r}, {b.y!r} ;\n  dimension {b.length!r}, {b.width!r} ;\n"
                       f"  power values {b.power!r} ;\n")
        return "\n".join(out)

    def check_inside(self, grid: Grid):
        for b in self.blocks:
            if b.x < -1e-9 or b.y < -1e-9 or b.x + b.length > grid.length + 1e-6 or b.y + b.width > grid.width + 1e-6:
                raise FloorplanError(f"block {b.name} ({b.x}, {b.y}, {b.length} x {b.width}) leaves the chip "
                                     f"{grid.length} x {grid.width} um")

    def check_overlap(self, grid: Grid = None):
        if grid is not None and not self.misaligned(grid):   # whole cells: count coverage per cell, O(blocks)
            count = np.zeros(grid.shape, np.int32)
            for b in self.blocks:
                c0, r0 = int(round(b.x / grid.dx)), int(round(b.y / grid.dy))
                c1, r1 = int(round((b.x + b.length) / grid.dx)), int(round((b.y + b.width) / grid.dy))
                count[r0:r1, c0:c1] += 1
            if count.max() > 1:
                r, c = np.argwhere(count > 1)[0]
                raise FloorplanError(f"blocks overlap in cell (row {r}, col {c})")
            return
        bs = sorted(self.blocks, key=lambda b: b.x)       # sweep along x: compare only blocks that can still overlap
        for i, a in enumerate(bs):
            for b in bs[i + 1:]:
                if b.x >= a.x + a.length - 1e-6:
                    break
                if (min(a.x + a.length, b.x + b.length) - max(a.x, b.x) > 1e-6 and
                        min(a.y + a.width, b.y + b.width) - max(a.y, b.y) > 1e-6):
                    raise FloorplanError(f"blocks {a.name} and {b.name} overlap")

    def misaligned(self, grid: Grid):
        """Names of blocks with an edge that is not on a cell boundary (a partly covered cell)."""
        r = np.array([(b.x, b.x + b.length, b.y, b.y + b.width) for b in self.blocks], dtype=float)
        ok = grid.on_x_line(r[:, 0]) & grid.on_x_line(r[:, 1]) & grid.on_y_line(r[:, 2]) & grid.on_y_line(r[:, 3])
        return [self.blocks[i].name for i in np.flatnonzero(~ok)]

    def snapped(self, grid: Grid):
        """Each block edge moved to the nearest cell boundary; total power per block kept."""
        out = []
        for b in self.blocks:
            x0, x1 = grid.snap_x(b.x), grid.snap_x(b.x + b.length)
            y0, y1 = grid.snap_y(b.y), grid.snap_y(b.y + b.width)
            if x1 <= x0 or y1 <= y0:
                raise FloorplanError(f"block {b.name} ({b.length} x {b.width} um) collapses when snapped to "
                                     f"{grid.dx} x {grid.dy} um cells")
            out.append(Block(b.name, x0, y0, x1 - x0, y1 - y0, b.power))
        return Floorplan(out)

    def aligned(self, grid: Grid, policy="error"):
        """Apply the die-edge policy: 'error' refuses partial cells, 'snap' snaps, 'allow' keeps the floorplan."""
        if policy not in EDGE_POLICIES:
            raise ValueError(f"edge policy must be one of {EDGE_POLICIES}, not {policy!r}")
        self.check_inside(grid)
        fp = self
        bad = self.misaligned(grid)
        if bad and policy == "error":
            raise FloorplanError(
                f"block edges off the {grid.dx} x {grid.dy} um grid: {', '.join(bad[:10])}"
                f"{' ...' if len(bad) > 10 else ''}. Partly covered cells get an area share of the power but a single "
                "material, which gives a die-edge artefact; snap the placement (edge_policy='snap') or use "
                "edge_policy='allow' deliberately")
        if bad and policy == "snap":
            fp = self.snapped(grid)
            fp.check_inside(grid)
        fp.check_overlap(grid)
        return fp

    def edge_cell_mask(self, grid: Grid):
        """(ny, nx) bool: cells crossed by (strictly containing) a block edge, i.e. partly covered by some block."""
        mask = np.zeros(grid.shape, bool)
        xe, ye = grid.x_edges, grid.y_edges
        for b in self.blocks:
            c0 = int(np.searchsorted(xe, b.x + 1e-6 * grid.dx, side="right")) - 1
            c1 = int(np.searchsorted(xe, b.x + b.length - 1e-6 * grid.dx, side="right")) - 1
            r0 = int(np.searchsorted(ye, b.y + 1e-6 * grid.dy, side="right")) - 1
            r1 = int(np.searchsorted(ye, b.y + b.width - 1e-6 * grid.dy, side="right")) - 1
            c0, c1 = max(c0, 0), min(c1, grid.nx - 1)
            r0, r1 = max(r0, 0), min(r1, grid.ny - 1)
            left, right = not grid.on_x_line(b.x), not grid.on_x_line(b.x + b.length)
            bottom, top = not grid.on_y_line(b.y), not grid.on_y_line(b.y + b.width)
            if left:
                mask[r0:r1 + 1, c0] = True
            if right:
                mask[r0:r1 + 1, c1] = True
            if bottom:
                mask[r0, c0:c1 + 1] = True
            if top:
                mask[r1, c0:c1 + 1] = True
        return mask
