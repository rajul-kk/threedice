"""Batch 3D-ICE solves with probe export (needs NumPy: `pip install threedice[batch]`).

    from threedice.batch import Stack, Block, Floorplan, Probes, cases_product, run_batch, load_batch
    stack = Stack.from_file("pkg.stk")
    cases = cases_product(stack, placements={"a": {"TOP": (0, 0)}}, power_maps={"m0": {"TOP": Floorplan([...])}})
    res = run_batch(stack, cases, Probes("TOP", x_um, y_um), out="batch.npz", cache_dir=".threedice_cache")
    res.probe_T          # (n_cases, n_probes) K;  res.field("TOP") -> (n_cases, ny, nx)

Design, formats and limits: docs/batch_api_design.md. This module only runs the solver binary as a separate process;
it does not change the build or the upstream patches.
"""
try:
    import numpy  # noqa: F401
except ImportError as e:  # the base package has no dependencies; batch is an extra
    raise ImportError("threedice.batch needs NumPy: pip install 'threedice[batch]'") from e

from .cache import Cache, case_key
from .floorplan import Block, Floorplan, FloorplanError
from .grid import Grid, GridError
from .probes import Probes, map_probes, probe_values
from .runner import (BatchError, BatchResult, Case, cases_product, load_batch, resolve_command, run_batch,
                     solver_identity)
from .stack import Stack, StackError
from .tmap import TmapError, read_axis, read_tmap

__all__ = ["Block", "BatchError", "BatchResult", "Cache", "Case", "Floorplan", "FloorplanError", "Grid", "GridError",
           "Probes", "Stack", "StackError", "TmapError", "case_key", "cases_product", "load_batch", "map_probes",
           "probe_values", "read_axis", "read_tmap", "resolve_command", "run_batch", "solver_identity"]
