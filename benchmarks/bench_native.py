"""Wall time of run_batch with backend='process' (3D-ICE-Emulator per case) and backend='native' (3D-ICE-Batch,
one factorisation per worker) on a placements x power maps batch, and the largest temperature difference.

usage: python bench_native.py <out.json> [--grid 50] [--placements 6] [--maps 4] [--workers 1 4]
Needs THREEDICE_EXECUTABLE / THREEDICE_BATCH_EXECUTABLE or the bundled binaries.
"""
import argparse
import json
import platform
import time

import numpy as np

from threedice.batch import Block, Floorplan, Stack, cases_product, run_batch

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
 chip length 10000, width 10000 ;
 cell length {cell}, width {cell} ;
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
"""
BASE = "base :\n position 0, 0 ;\n dimension 10000, 10000 ;\n power values 2.0 ;\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--grid", type=int, default=50, help="cell size in um")
    ap.add_argument("--placements", type=int, default=6)
    ap.add_argument("--maps", type=int, default=4)
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 4])
    ap.add_argument("--backends", nargs="+", default=["process", "native"], choices=["process", "native"])
    a = ap.parse_args()
    stack = Stack(STK.format(cell=a.grid), files={"upper.flp": BASE, "lower.flp": BASE})
    rng = np.random.default_rng(0)
    c = a.grid
    placements = {f"p{i}": {"UPPER": (float(rng.integers(0, 60) * c), float(rng.integers(0, 60) * c))}
                  for i in range(a.placements)}
    maps = {}
    for j in range(a.maps):
        # three 20 x 20-cell blocks in separate 30-cell columns, so they never overlap
        blocks = [Block(f"b{k}", (30 * k + int(rng.integers(0, 8))) * c, int(rng.integers(0, 80)) * c, 20 * c, 20 * c,
                        float(rng.uniform(0.5, 4.0))) for k in range(3)]
        maps[f"m{j}"] = {"UPPER": Floorplan(blocks)}
    cases = cases_product(stack, placements, maps)
    rows, fields = [], {}
    for backend in a.backends:
        for w in a.workers:
            t = time.perf_counter()
            res = run_batch(stack, cases, outputs=["UPPER", "LOWER"], field_dtype="float64", workers=w, backend=backend)
            dt = time.perf_counter() - t
            fields[backend] = res.field("UPPER")
            solves = list(res.meta["solves"].values())
            rows.append({"backend": backend, "workers": w, "cases": len(cases), "wall_s": dt,
                         "per_case_s": dt / len(cases), "grid": list(stack.grid.shape),
                         "factor_s": max((s.get("factor_seconds", 0) for s in solves), default=0),
                         "median_solve_s": float(np.median([s["seconds"] for s in solves]))})
            print(f"{backend:<8} workers {w}: {dt:7.2f} s for {len(cases)} cases ({dt / len(cases):.3f} s/case)", flush=True)
    if set(fields) != {"process", "native"}:
        with open(a.out, "w") as f:
            json.dump({"rows": rows, "machine": platform.machine(), "python": platform.python_version()}, f, indent=1)
        return
    diff = float(np.max(np.abs(fields["native"] - fields["process"])))
    print(f"max |native - process| = {diff:.2e} K (process Tmaps are printed with 3 decimals)")
    for w in a.workers:
        p = next(r for r in rows if r["backend"] == "process" and r["workers"] == w)
        n = next(r for r in rows if r["backend"] == "native" and r["workers"] == w)
        print(f"workers {w}: native is {p['wall_s'] / n['wall_s']:.1f}x faster")
    with open(a.out, "w") as f:
        json.dump({"rows": rows, "max_abs_diff_K": diff, "machine": platform.machine(),
                   "python": platform.python_version()}, f, indent=1)


if __name__ == "__main__":
    main()
