"""threedice.batch against the real 3D-ICE binary.

Uses $THREEDICE_EXECUTABLE when set (e.g. "wsl /home/<you>/.../3D-ICE-Emulator" on Windows), else the binary bundled
with the installed wheel; skipped when neither exists or NumPy is missing.
"""
import os

import pytest

np = pytest.importorskip("numpy")
from threedice.batch import (Block, Case, Floorplan, Probes, Stack, cases_product, run_batch,  # noqa: E402
                             read_tmap)


def _have_solver():
    if os.environ.get("THREEDICE_EXECUTABLE"):
        return True
    try:
        import threedice
        threedice.executable()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _have_solver(), reason="no 3D-ICE binary: threedice wheel not installed here and "
                                "THREEDICE_EXECUTABLE not set")

HERE = os.path.dirname(os.path.abspath(__file__))
MINI = os.path.join(HERE, "reference", "cases", "mini_steady_2die")
EXPECTED = os.path.join(HERE, "reference", "expected", "mini_steady_2die")

STK = """material SI :
 thermal conductivity 1.30e-4 ;
 volumetric heat capacity 1.628e-12 ;
material BEOL :
 thermal conductivity 2.25e-6 ;
 volumetric heat capacity 2.175e-12 ;
top heat sink :
 heat transfer coefficient 5.0e-8 ;
 temperature 300 ;
bottom heat sink :
 heat transfer coefficient 1.0e-9 ;
 temperature 300 ;
dimensions :
 chip length 2000, width 1500 ;
 cell length 100, width 100 ;
die D :
 layer 10 BEOL ;
 source 2 SI ;
 layer 80 SI ;
stack :
 die UPPER D floorplan "upper.flp" ;
 die LOWER D floorplan "lower.flp" ;
solver :
 steady ;
 initial temperature 300 ;
output :
 Tmap ( UPPER, "u.txt", final ) ;
"""
BASE = "base :\n position 0, 0 ;\n dimension 2000, 1500 ;\n power values 0.5 ;\n"


def small_stack():
    return Stack(STK, files={"upper.flp": BASE, "lower.flp": BASE})


def test_reference_case_field_is_the_recorded_3dice_output():
    """The batch path (render, numofcores 1, parse) reproduces the single-thread reference Tmaps of this package."""
    stack = Stack.from_file(os.path.join(MINI, "two_die.stk"))
    probes = Probes(["UPPER", "LOWER", "LOWER"], [750, 1000, 3999], [450, 1000, 2999])
    res = run_batch(stack, [Case("ref")], probes, outputs=["UPPER", "LOWER"], field_dtype="float64", workers=1)
    for e, f in (("UPPER", "upper_map.txt"), ("LOWER", "lower_map.txt")):
        assert np.array_equal(res.field(e)[0], read_tmap(os.path.join(EXPECTED, f)))
    cell = res["probe_cell"]
    for p, e in enumerate(res["probe_element"]):
        assert res.probe_T[0, p] == res.field(str(e))[0, cell[p, 0], cell[p, 1]]


def test_placements_by_power_maps_probes_match_field_and_hotspot_follows_placement(tmp_path):
    stack = small_stack()
    hot = Floorplan([Block("hot", 0, 0, 400, 300, 3.0), Block("rest", 200, 300, 200, 100, 0.5)])
    grid_pm = np.zeros((3, 4))
    grid_pm[1, 2] = 1.5
    placements = {"left": {"UPPER": (100, 200)}, "right": {"UPPER": (1400, 1000)}}
    cases = cases_product(stack, placements, {"blocks": {"UPPER": hot}, "cells": {"UPPER": grid_pm}})
    xs, ys = np.meshgrid(np.linspace(0, 2000, 7), np.linspace(0, 1500, 5))
    probes = Probes("UPPER", xs.ravel(), ys.ravel())
    out = tmp_path / "b.npz"
    res = run_batch(stack, cases, probes, outputs=["LOWER"], out=str(out), cache_dir=str(tmp_path / "c"), workers=2,
                    field_dtype="float64")
    f = res.field("UPPER")
    cell = res["probe_cell"]
    assert np.array_equal(res.probe_T, f[:, cell[:, 0], cell[:, 1]])
    # the hottest cell lies under the placed hot block (orientation of rows/columns is right)
    i = res.case_names.index("left__blocks")
    r, c = np.unravel_index(np.argmax(f[i]), f[i].shape)
    assert 100 <= (c + 0.5) * 100 <= 500 and 200 <= (r + 0.5) * 100 <= 500
    i = res.case_names.index("right__cells")
    r, c = np.unravel_index(np.argmax(f[i]), f[i].shape)
    assert (r, c) == (1 + 10, 2 + 14)                        # cell (1, 2) of the map, shifted by (1400, 1000) um
    assert (res["probe_edge_cell"] == 0).all()
    # bilinear from the same cache: no new solves, values consistent with the stored field
    res2 = run_batch(stack, cases, probes, interp="bilinear", cache_dir=str(tmp_path / "c"), field_dtype="float64")
    assert all(v["cached"] for v in res2.meta["solves"].values())
    idx, w = res2["probe_index"], res2["probe_weight"]
    flat = res2.field("UPPER").reshape(len(cases), -1)
    assert np.allclose(res2.probe_T, np.einsum("npk,pk->np", flat[:, idx], w), atol=1e-12)


def test_superposition_of_power_maps_holds_at_probes():
    """Linear materials and heat sinks: T(p1 + p2) - T0 = (T(p1) - T0) + (T(p2) - T0), up to the 0.001 K printing."""
    stack = Stack(STK.replace("power values 0.5", "power values 0.0"), files={})
    zero = Floorplan([Block("z", 0, 0, 2000, 1500, 0.0)])
    p1 = Floorplan([Block("a", 200, 300, 300, 300, 2.0), Block("z", 1000, 1000, 100, 100, 0.0)])
    p2 = Floorplan([Block("a", 200, 300, 300, 300, 0.0), Block("z", 1000, 1000, 100, 100, 1.0)])
    p12 = Floorplan([Block("a", 200, 300, 300, 300, 2.0), Block("z", 1000, 1000, 100, 100, 1.0)])
    cases = [Case(n, {"UPPER": fp, "LOWER": zero}) for n, fp in (("p1", p1), ("p2", p2), ("p12", p12))]
    probes = Probes("UPPER", [350, 1050, 1900, 10], [450, 1050, 1400, 10])
    res = run_batch(stack, cases, probes, interp="bilinear")
    t1, t2, t12 = res.probe_T - 300.0
    assert np.max(np.abs(t12 - (t1 + t2))) <= 2e-3
    assert t12.max() > 1.0                                    # the test is not trivially satisfied
