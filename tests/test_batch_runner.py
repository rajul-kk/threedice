"""threedice.batch.run_batch with a stand-in solver (tests/_fake_3dice.py): caching, probe vs field, output file."""
import os
import sys

import pytest

np = pytest.importorskip("numpy")
from threedice.batch import (BatchError, Block, Case, Floorplan, FloorplanError, Probes, Stack,  # noqa: E402
                             cases_product, load_batch, run_batch)

FAKE = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fake_3dice.py")]

STK = """material SI :
 thermal conductivity 1.30e-4 ;
 volumetric heat capacity 1.628e-12 ;
bottom heat sink :
 heat transfer coefficient 2.0e-8 ;
 temperature 300 ;
dimensions :
 chip length 1000, width 800 ;
 cell length 100, width 100 ;
die D :
 source 50 SI ;
stack :
 die TOP D floorplan "top.flp" ;
 die BOT D floorplan "bot.flp" ;
solver :
 steady ;
 initial temperature 300 ;
output :
 Tmap ( TOP, "x.txt", final ) ;
"""
FLP = "blk :\n position 0, 0 ;\n dimension 200, 200 ;\n power values 1.0 ;\n"


@pytest.fixture
def stack():
    return Stack(STK, files={"top.flp": FLP, "bot.flp": FLP})


@pytest.fixture
def log(tmp_path, monkeypatch):
    p = tmp_path / "solves"
    p.mkdir()
    monkeypatch.setenv("FAKE_3DICE_LOG", str(p))
    return lambda: len(os.listdir(p))


def product(stack):
    placements = {"a": {"TOP": (0, 0)}, "b": {"TOP": (300, 200)}}
    pm0 = {"TOP": Floorplan([Block("core", 0, 0, 200, 200, 2.0)])}
    pm1 = np.zeros((2, 3))
    pm1[1, 2] = 0.7
    return cases_product(stack, placements, {"m0": pm0, "m1": {"TOP": pm1}})


PROBES = Probes(["TOP", "TOP", "BOT", "TOP"], [50, 999, 420, 555], [50, 799, 333, 400], names=["s0", "s1", "s2", "s3"])


def test_probe_values_equal_full_field_cell_mode(stack, log, tmp_path):
    res = run_batch(stack, product(stack), PROBES, outputs=["BOT"], executable=FAKE, solver_id="fake",
                    field_dtype="float64", workers=2, out=str(tmp_path / "b.npz"))
    assert res.case_names == ["a__m0", "a__m1", "b__m0", "b__m1"]
    assert res.probe_T.shape == (4, 4) and log() == 4
    cell = res["probe_cell"]
    for p, e in enumerate(res["probe_element"]):
        f = res.field(str(e))
        assert np.array_equal(res.probe_T[:, p], f[:, cell[p, 0], cell[p, 1]])
    assert cell.tolist() == [[0, 0], [7, 9], [3, 4], [4, 5]]
    assert (res["probe_edge_cell"][:, [0, 1, 3]] == 0).all()     # generated floorplans, all aligned
    assert (res["probe_edge_cell"][:, 2] == -1).all()            # BOT keeps the stack's own floorplan: unknown
    # the placement offset changes the generated floorplan, hence the field
    assert not np.array_equal(res.field("TOP")[0], res.field("BOT")[0])
    # saved file round-trips
    back = load_batch(str(tmp_path / "b.npz"))
    assert back.case_names == res.case_names and np.array_equal(back.probe_T, res.probe_T)
    assert back.meta["interp"] == "cell" and back.meta["solves"]["a__m0"]["cached"] is False


def test_bilinear_probes_match_interpolated_field_and_float32_storage(stack, log):
    res = run_batch(stack, product(stack)[:2], PROBES, executable=FAKE, solver_id="fake", interp="bilinear")
    assert res.field("TOP").dtype == np.float32
    idx, w = res["probe_index"], res["probe_weight"]
    for p, e in enumerate(res["probe_element"]):
        f = res.field(str(e)).astype(np.float64).reshape(2, -1)
        assert np.allclose(res.probe_T[:, p], (f[:, idx[p]] * w[p]).sum(axis=1), atol=4e-5)


def test_cache_hits_skip_solves_and_new_probes_do_not_resolve(stack, log, tmp_path):
    cache = str(tmp_path / "cache")
    cases = product(stack)
    r1 = run_batch(stack, cases, PROBES, executable=FAKE, solver_id="fake", cache_dir=cache, field_dtype="float64")
    assert log() == 4
    r2 = run_batch(stack, cases, Probes("TOP", [10, 20], [30, 40]), executable=FAKE, solver_id="fake",
                   cache_dir=cache, field_dtype="float64")
    assert log() == 4                                       # all from cache
    assert np.array_equal(r2.field("TOP"), r1.field("TOP"))
    assert all(v["cached"] for v in r2.meta["solves"].values())
    assert list(r2["cache_keys"]) == list(r1["cache_keys"])
    # a changed case, a changed solver id: new keys, new solves
    changed = cases[:1] + [Case("new", {"TOP": Floorplan([Block("core", 0, 0, 200, 200, 2.5)])})]
    run_batch(stack, changed, PROBES, executable=FAKE, solver_id="fake", cache_dir=cache)
    assert log() == 5
    run_batch(stack, cases[:1], PROBES, executable=FAKE, solver_id="fake-v2", cache_dir=cache)
    assert log() == 6


def test_cache_entry_grows_with_new_output_elements(stack, log, tmp_path):
    cache = str(tmp_path / "cache")
    cases = product(stack)[:1]
    run_batch(stack, cases, outputs=["TOP"], executable=FAKE, solver_id="fake", cache_dir=cache)
    assert log() == 1
    r = run_batch(stack, cases, outputs=["BOT"], executable=FAKE, solver_id="fake", cache_dir=cache)
    assert log() == 2 and list(r["outputs"]) == ["BOT"]       # BOT was missing: re-solved for TOP and BOT
    run_batch(stack, cases, outputs=["TOP", "BOT"], executable=FAKE, solver_id="fake", cache_dir=cache)
    assert log() == 2


def test_corrupt_cache_entry_is_a_miss(stack, log, tmp_path):
    cache = tmp_path / "cache"
    r = run_batch(stack, product(stack)[:1], PROBES, executable=FAKE, solver_id="fake", cache_dir=str(cache))
    key = str(r["cache_keys"][0])
    (cache / key[:2] / (key + ".npz")).write_bytes(b"not a zip")
    run_batch(stack, product(stack)[:1], PROBES, executable=FAKE, solver_id="fake", cache_dir=str(cache))
    assert log() == 2
    assert not [f for f in os.listdir(cache / key[:2]) if f.startswith(".tmp_")]


def test_identical_cases_are_solved_once(stack, log):
    fp = {"TOP": Floorplan([Block("core", 0, 0, 200, 200, 2.0)])}
    res = run_batch(stack, [Case("x", fp), Case("y", fp)], PROBES, executable=FAKE, solver_id="fake")
    assert log() == 1 and np.array_equal(res.probe_T[0], res.probe_T[1])


def test_misaligned_placement_refused_before_any_solve_snap_and_allow(stack, log):
    cases = cases_product(stack, {"off": {"TOP": (142, 0)}}, {"m": {"TOP": Floorplan([Block("c", 0, 0, 200, 200, 1)])}})
    with pytest.raises(FloorplanError, match="off the"):
        run_batch(stack, cases, PROBES, executable=FAKE, solver_id="fake")
    assert log() == 0
    res = run_batch(stack, cases, PROBES, executable=FAKE, solver_id="fake", edge_policy="snap")
    assert res["probe_edge_cell"][0, 0] == 0
    res = run_batch(stack, cases, Probes("TOP", [150, 250, 345, 500], [50, 50, 150, 50]), executable=FAKE,
                    solver_id="fake", edge_policy="allow")
    assert res["probe_edge_cell"][0].tolist() == [1, 0, 1, 0]   # cells 1 and 3 are partly covered (x 142..342)


def test_failures_are_collected_and_successes_cached(stack, log, tmp_path):
    cache = str(tmp_path / "cache")
    cases = [Case("ok", {"TOP": Floorplan([Block("core", 0, 0, 200, 200, 2.0)])}),
             Case("bad", {"TOP": Floorplan([Block("FAIL", 0, 0, 200, 200, 2.0)])})]
    with pytest.raises(BatchError) as ei:
        run_batch(stack, cases, PROBES, executable=FAKE, solver_id="fake", cache_dir=cache)
    assert list(ei.value.failures) == ["bad"] and "exit 3" in ei.value.failures["bad"]
    run_batch(stack, cases[:1], PROBES, executable=FAKE, solver_id="fake", cache_dir=cache)
    assert log() == 2                                       # 'ok' came from the cache


def test_rendered_stack_forces_single_thread(stack, tmp_path):
    run_batch(stack, product(stack)[:1], PROBES, executable=FAKE, solver_id="fake", work_dir=str(tmp_path),
              keep_dirs=True)
    (d,) = [p for p in tmp_path.iterdir() if p.name.startswith("threedice_batch_")]
    text = (d / "stack.stk").read_text()
    assert "numofcores 1 ;" in text and 'floorplan "fp_TOP.flp"' in text and (d / "bot.flp").exists()
