"""Batch 3D-ICE solves with probe export (needs NumPy). Design: docs/batch_api_design.md."""
from .grid import Grid, GridError
from .probes import Probes, map_probes, probe_values
