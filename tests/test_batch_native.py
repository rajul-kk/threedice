"""threedice.batch with backend='native' (3D-ICE-Batch: factorise once, solve every floorplan) against the process
backend (3D-ICE-Emulator once per case).

Uses $THREEDICE_BATCH_EXECUTABLE / $THREEDICE_EXECUTABLE when set (e.g. "wsl /home/<you>/.../3D-ICE-Batch"), else the
binaries bundled with the installed wheel; skipped when either is missing or NumPy is missing.
"""
import os

import pytest

np = pytest.importorskip("numpy")
from threedice.batch import BatchError, Block, Case, Floorplan, Probes, Stack, cases_product, run_batch  # noqa: E402

from test_batch_solver import MINI, STK, small_stack  # noqa: E402


def _have(env, fn):
    if os.environ.get(env):
        return True
    try:
        import threedice
        getattr(threedice, fn)()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_have("THREEDICE_BATCH_EXECUTABLE", "batch_executable") and _have("THREEDICE_EXECUTABLE", "executable")),
    reason="needs both 3D-ICE-Batch and 3D-ICE-Emulator")

TMAP_ROUNDING = 5.0e-4 + 1e-9          # 3D-ICE-Emulator prints temperatures with three decimals


def placement_cases(stack):
    hot = Floorplan([Block("hot", 0, 0, 400, 300, 3.0), Block("rest", 200, 300, 200, 100, 0.5)])
    grid_pm = np.zeros((3, 4))
    grid_pm[1, 2] = 1.5
    placements = {"left": {"UPPER": (100, 200)}, "mid": {"UPPER": (800, 600)}, "right": {"UPPER": (1400, 1000)}}
    return cases_product(stack, placements, {"blocks": {"UPPER": hot}, "cells": {"UPPER": grid_pm}})


@pytest.mark.parametrize("workers", [1, 2])
def test_native_matches_the_emulator_case_by_case(workers):
    stack = small_stack()
    cases = placement_cases(stack)
    xs, ys = np.meshgrid(np.linspace(0, 2000, 7), np.linspace(0, 1500, 5))
    probes = Probes("UPPER", xs.ravel(), ys.ravel())
    ref = run_batch(stack, cases, probes, outputs=["LOWER"], field_dtype="float64", workers=2)
    nat = run_batch(stack, cases, probes, outputs=["LOWER"], field_dtype="float64", workers=workers, backend="native")
    assert nat.case_names == ref.case_names
    for e in ("UPPER", "LOWER"):
        assert np.max(np.abs(nat.field(e) - ref.field(e))) <= TMAP_ROUNDING
    assert np.max(np.abs(nat.probe_T - ref.probe_T)) <= TMAP_ROUNDING
    solves = nat.meta["solves"].values()
    assert all(s["backend"] == "native" for s in solves)
    # one factorisation per solver process, shared by all its cases
    assert sum(1 / s["session_cases"] for s in solves) == pytest.approx(workers)
    assert nat.meta["backend"] == "native" and nat.meta["solver_id"].startswith("native:")


def test_native_reference_case_matches_the_recorded_output_to_print_precision():
    from test_batch_solver import EXPECTED, read_tmap
    stack = Stack.from_file(os.path.join(MINI, "two_die.stk"))
    res = run_batch(stack, [Case("ref")], outputs=["UPPER", "LOWER"], field_dtype="float64", backend="native")
    for e, f in (("UPPER", "upper_map.txt"), ("LOWER", "lower_map.txt")):
        assert np.max(np.abs(res.field(e)[0] - read_tmap(os.path.join(EXPECTED, f)))) <= TMAP_ROUNDING


def test_superposition_holds_to_solver_precision():
    """Unrounded fields make superposition exact up to the linear solve (the process backend allows 2e-3 K)."""
    stack = Stack(STK.replace("power values 0.5", "power values 0.0"), files={})
    zero = Floorplan([Block("z", 0, 0, 2000, 1500, 0.0)])
    p1 = Floorplan([Block("a", 200, 300, 300, 300, 2.0), Block("z", 1000, 1000, 100, 100, 0.0)])
    p2 = Floorplan([Block("a", 200, 300, 300, 300, 0.0), Block("z", 1000, 1000, 100, 100, 1.0)])
    p12 = Floorplan([Block("a", 200, 300, 300, 300, 2.0), Block("z", 1000, 1000, 100, 100, 1.0)])
    cases = [Case(n, {"UPPER": fp, "LOWER": zero}) for n, fp in (("p1", p1), ("p2", p2), ("p12", p12))]
    res = run_batch(stack, cases, outputs=["UPPER"], field_dtype="float64", backend="native")
    t1, t2, t12 = res.field("UPPER") - 300.0
    assert np.max(np.abs(t12 - (t1 + t2))) <= 1e-9
    assert t12.max() > 1.0


def test_cases_with_different_extra_files_get_their_own_factorisation():
    stack = small_stack()
    fp = Floorplan([Block("hot", 0, 0, 400, 300, 3.0)])
    hot_lower = "base :\n position 0, 0 ;\n dimension 2000, 1500 ;\n power values 4.0 ;\n"
    cases = [Case("a", {"UPPER": fp}), Case("b", {"UPPER": fp}, files={"lower.flp": hot_lower})]
    ref = run_batch(stack, cases, outputs=["LOWER"], field_dtype="float64")
    nat = run_batch(stack, cases, outputs=["LOWER"], field_dtype="float64", backend="native")
    assert np.max(np.abs(nat.field("LOWER") - ref.field("LOWER"))) <= TMAP_ROUNDING
    assert nat.field("LOWER")[1].max() > nat.field("LOWER")[0].max() + 1.0
    assert [s["session_cases"] for s in nat.meta["solves"].values()] == [1, 1]


def test_native_and_process_results_do_not_share_cache_entries(tmp_path):
    stack = small_stack()
    cases = placement_cases(stack)[:2]
    run_batch(stack, cases, cache_dir=str(tmp_path), outputs=["UPPER"])
    nat = run_batch(stack, cases, cache_dir=str(tmp_path), outputs=["UPPER"], backend="native")
    assert not any(s["cached"] for s in nat.meta["solves"].values())
    again = run_batch(stack, cases, cache_dir=str(tmp_path), outputs=["UPPER"], backend="native")
    assert all(s["cached"] for s in again.meta["solves"].values())


def test_a_solver_that_cannot_start_fails_every_case_of_its_session(tmp_path):
    bad = tmp_path / "not-a-solver"
    bad.write_text("#!/bin/sh\necho ERR nope\nexit 1\n")
    bad.chmod(0o755)
    with pytest.raises(BatchError) as exc:
        run_batch(small_stack(), placement_cases(small_stack())[:3], outputs=["UPPER"], backend="native",
                  executable=str(bad), solver_id="test")
    assert len(exc.value.failures) == 3 and all("did not start" in m for m in exc.value.failures.values())


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="backend"):
        run_batch(small_stack(), [Case("x")], backend="threads")
