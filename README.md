# threedice

**Unofficial redistribution of 3D-ICE; not affiliated with or endorsed by EPFL ESL.**
3D-ICE is developed by the Embedded Systems Laboratory (ESL), EPFL: https://github.com/esl-epfl/3d-ice
(upstream commit `4953952a1ef6d38807ff307212a6f15e5b2ef935`, the one this package is pinned to). All credit for the
simulator belongs to its authors (listed in `NOTICE`); this package only compiles it and wraps the result as a wheel.
Problems with the simulator itself belong upstream; problems with the packaging belong in this repository.

The 3D-ICE 4.0 thermal simulator (`3D-ICE-Emulator`) as a pip-installable package. It ships one compiled binary, built
from the upstream commit above, so a pinned, reproducible 3D-ICE needs no compiler, no SuperLU and no `make`.

Licence: **GPL-3.0-or-later** (3D-ICE's own licence). The complete corresponding source is in the sdist.

**Please cite the 3D-ICE papers** (full references in `NOTICE`; upstream asks that publications 1 to 4 all be cited):

1. K. Zhu, D. Huang, L. Costero, D. Atienza, "3D-ICE 4.0: Accurate and efficient thermal modeling for 2.5D/3D heterogeneous chiplet systems", DATE 2026.
2. F. Terraneo, A. Leva, W. Fornaciari, M. Zapater, D. Atienza, "3D-ICE 3.0: efficient nonlinear MPSoC thermal simulation with pluggable heat sink models", IEEE TCAD 40, 2021.
3. A. Sridhar, A. Vincenzi, D. Atienza, T. Brunschwiler, "3D-ICE: A Compact Thermal Model for Early-Stage Design of Liquid-Cooled ICs", IEEE Trans. Computers 63(10), 2014, doi:10.1109/TC.2013.127.
4. A. Sridhar, A. Vincenzi, M. Ruggiero, T. Brunschwiler, D. Atienza, "3D-ICE: Fast compact transient thermal modeling for 3D ICs with inter-tier liquid cooling", ICCAD 2010, doi:10.1109/ICCAD.2010.5653749.

Packaged by Rajul Kabeer (https://github.com/rajul-kk/threedice). Package version `4.0.0.postN` = upstream 3D-ICE 4.0 plus later
commits up to the pinned sha, packaging revision N.

```
pip install threedice            # prebuilt wheels: see "Platforms" below
python -c "import threedice; print(threedice.executable(), threedice.ice_commit())"
3d-ice-emulator model.stk        # same arguments as 3D-ICE-Emulator; run it in the stk's directory
```

API: `threedice.executable()` (path of the bundled binary), `threedice.ice_commit()` (the full upstream sha),
`threedice.build_info()` (contents of `threedice/_build_info.json`: upstream and SuperLU_MT commits, build date,
toolchain, applied patches with sha256), `3d-ice-emulator` (console script, execs the binary).

Not included: the SystemC wrapper, `3D-ICE-Server`/`3D-ICE-Client`, the Python heat-sink plugin.

## What is built, and from what

| piece | source | note |
|---|---|---|
| 3D-ICE | `esl-epfl/3d-ice` @ `4953952a1ef6d38807ff307212a6f15e5b2ef935` | patches in `patches/` |
| SuperLU_MT (OpenMP), static | `xiaoyeli/superlu_mt` @ `e60ed2c1dd491e1832d783f519c1851288f9d422` | patch in `patches/superlu_mt/` |
| OpenBLAS, libgomp, libgfortran | from the manylinux_2_28 container (OpenBLAS from AlmaLinux 8 `openblas-devel`) | bundled by `auditwheel repair` |

Note on SuperLU_MT: 3D-ICE's own tree ships `superlu_mt-4.0.0.zip`, but the pinned WSL build (`~/3d-ice-4.0`)
actually linked a git checkout of SuperLU_MT master (`e60ed2c`, 2026-03-29, which fixes the 64-bit-int prototype
mismatches of the zip). The wheel reproduces that tree, not the zip.

Patches (all build fixes, none changes the numerics of the algorithm):

1. `0001-drop-werror.patch`: removes `-Werror` (a newer compiler turns upstream warnings into errors). Same as the local patch in `docs/installation.md`, plus `bin/Makefile`.
2. `0002-luindex-int.patch`: `LUIndex_t` from `long` to `int`, to match SuperLU_MT's `int_t` (the second patch in `docs/installation.md`).
3. `superlu_mt/0001-openmp-openblas-make-inc.patch`: SuperLU_MT `make.inc` for OpenMP + `-lopenblas` (what `install-superlumt.sh` does by hand).

## Source and the GPL

The upstream source is **not** vendored in this git repository. `upstream.json` pins both repositories to full
shas. `build_support.py` fetches each at exactly that sha (`git fetch <sha>`), fails if `HEAD` differs, and makes a
`git archive` tarball. **The sdist contains both tarballs** under `upstream/` (complete corresponding source) plus
`patches/`, and building from the sdist needs no network. Whoever receives a wheel can get the matching source
from the sdist of the same version (or from the two upstream commits plus `patches/`).

## Building

Linux x86_64 wheel (the official manylinux_2_28 container; Docker on Windows: use PowerShell, Git Bash mangles paths):

```
docker run --rm -v <repo>:/src:ro -v <outdir>:/out quay.io/pypa/manylinux_2_28_x86_64 bash /src/build_manylinux.sh
```

This builds the sdist, then builds the wheel *from the sdist*, then runs `auditwheel repair`. The wheel is tagged
`py3-none-manylinux_2_28_x86_64`. Build dependencies: gcc, make, bison, flex, patch, git, OpenBLAS (with `cblas.h`).
From a source checkout on any Linux with those: `pip install .`. CI builds all platforms (`.github/workflows/build.yml`); releasing is described in `RELEASING.md`.

Verification record (commands, numbers, environment): `VERIFICATION.md`.

## Platforms

* **Linux x86_64**: built and verified locally (see `VERIFICATION.md`); built by CI as well.
* **Linux aarch64**: built by CI (`ubuntu-24.04-arm`, manylinux_2_28). **Not run by us before the first CI run.**
* **macOS arm64 and x86_64**: built by CI (Homebrew gcc + OpenBLAS, `delocate`). **Never built or run by us; untested.**
* **Windows (experimental)**: CI tries an MSYS2/MinGW-w64 build of the emulator only (`continue-on-error`). **Untested**; the
  patch it needs is `patches/windows/0001-mingw-emulator-only.patch` (drops `getrusage` and `network_socket.c`, which only the
  server/client use). A wheel is published for Windows only once that job has produced one that passes its smoke test and
  has been checked by hand. Until then, install inside WSL and let iceforge's WSL backend call it:

  ```
  wsl -e python3 -m venv ~/ice-venv && wsl -e ~/ice-venv/bin/pip install threedice
  # then point iceforge at it (path inside WSL):
  set ICE_EXECUTABLE=wsl /home/<you>/ice-venv/lib/python3.X/site-packages/threedice/bin/3D-ICE-Emulator
  ```

  (`wsl -e ~/ice-venv/bin/python -c "import threedice; print(threedice.executable())"` prints the exact path.) The pin
  check reads the commit from the package's `_build_info.json` for such paths.

## Cross-platform agreement

Do all wheels give the same temperatures as the Linux x86_64 wheel? `tests/xplat_check.py` (run by every wheel job of
`build.yml` after the build, from a fresh install of the built wheel) solves six small cases and compares every output file
with `tests/reference/expected/`, the files written by the Linux x86_64 wheel (`tests/reference/reference.json`: wheel sha256,
binary sha256, platform; the reference was produced locally in Docker from the cibuildwheel-built wheel, not by CI). Cases:
3D-ICE's own `bin/example_steady.stk` (microchannels, 5 outputs), `example_steady_material.stk` (incl. a 3-D VTK map),
`example_transient_nonuniform.stk`, plus two hand-written two-die stacks, one steady and one transient (100 um cells; Tmap and
Tflp outputs). The 3D-ICE example inputs are copies of GPL-3.0-or-later upstream files (line endings normalised to LF).

Method: outputs are text printed with limited digits, so the comparison is on the **parsed numbers** (every numeric token of
every file: abs diff, rel diff = abs diff / |reference|); "bit-identical" means the file bytes are equal (after CRLF -> LF).
**Tolerance, fixed before the first run: a job fails if any number differs by more than 1e-6 K absolute.** It has not been
changed. A difference smaller than the printed precision would not be seen. Each job writes `xplat-<platform>.json` (artifact)
and `::notice::` annotations.

### Threads

3D-ICE reads the thread count from the stack file: `numofcores N ;` in the `solver:` section (default 1;
`bison/stack_description_parser.y`, rule `optional_numofcores`), applied by `set_parallel_cores()` in `sources/thermal_data.c`
(`omp_set_num_threads(N)`, refused above `omp_get_max_threads()`) and passed to SuperLU_MT as `nprocs` in
`system_matrix_build`. No rebuild is needed. The check reruns every case with `numofcores` 1, 2 and 4 (and `OMP_NUM_THREADS=N`, so
N is honoured on runners with fewer cores) and compares each with the **single-thread** Linux x86_64 reference. A sixth case,
`big_steady_4die` (4 dies, 50 um cells, about 57k nodes, 2 to 15 s single-threaded), was added for the thread comparison. 3D-ICE's own
`stack.stk` was tried for that and dropped: with this build it dies inside SuperLU_MT ("Storage for U columns exceeded", exit 255)
at any thread count.

Rules fixed before the run: bit-identity across thread counts was expected but is not a pass condition; a job fails only above
1e-6 K absolute (unchanged); nothing was loosened afterwards.

### Measured (runs [38267450759](https://github.com/rajul-kk/threedice/actions/runs/38267450759) single thread, commit 5cfca52;
[38304627156](https://github.com/rajul-kk/threedice/actions/runs/38304627156) commit 1ec7844 and
[38305276202](https://github.com/rajul-kk/threedice/actions/runs/38305276202) commit e488e90, threads 1, 2, 4)

Runner cores: linux x86_64 4, linux aarch64 4, macOS x86_64 4, macOS arm64 3, Windows 4.

**Single thread, platform vs the Linux x86_64 reference: bit-identical on every file of every case on every platform (max abs
diff 0.0 K, max rel 0).**

**Multi-threaded, per platform x threads x case** (bit-identical / max abs diff in K):

| platform | threads | 6 cases, run 38305276202 | 6 cases, run 38304627156 |
|---|---|---|---|
| linux x86_64 | 1, 2, 4 | all identical, 0.0 | (log not read; the job passed) |
| linux aarch64 | 1, 2, 4 | all identical, 0.0 | (job passed) |
| macOS x86_64 | 1, 2, 4 | all identical, 0.0 | (job passed) |
| macOS arm64 (3 cores) | 1, 2 | all identical, 0.0 | (job passed) |
| macOS arm64 (3 cores) | 4 | **`mini_steady_2die`: NOT identical, max abs 3.527 K, max rel 1.1e-2** (lower_map 3.401, upper_map 3.527, lower_flp 0.834); the other 5 cases identical | identical |
| Windows | 1, 2 | all identical, 0.0 | all identical, 0.0 |
| Windows | 4 | all identical, 0.0 | **`example_transient_nonuniform`: NOT identical, max abs 0.023 K, max rel 7.0e-5**; the other 5 cases identical |

**Finding: multi-threaded solves are not reproducible.** Twice, one (platform, case, 4 threads) result differed from the
reference by far more than 1e-6 K (3.5 K and 0.023 K), failing the job both times, and the same cell was clean in the other run. The
macOS arm64 and Windows wheels are not the cause by themselves: in a local Docker check on the Linux x86_64 wheel (12 cores),
`mini_steady_2die` with `numofcores 4` differed from the single-thread result in 1 of 300 runs unrestricted and in 4 of 300 runs
restricted to 2 CPUs; with `numofcores 2` in 1 of 300 runs (0 of 300 on 2 CPUs); pinned to a single CPU, 0 of 300. Single-thread runs have never differed.
That points at a rare race in the threaded SuperLU_MT factorisation (not investigated further; no patch made). Practical
consequence: for results that must be reproducible, use `numofcores 1`. The CI check keeps the 1e-6 K tolerance, so the macOS arm64 and
Windows jobs can fail intermittently at 4 threads; Windows is `continue-on-error`.

Limits: six cases; a difference below the printed precision would not be seen; the race rate was measured on one machine only.
Different compilers and BLAS libraries are used per platform (gcc 14 + OpenBLAS on Linux, Homebrew gcc + OpenBLAS on macOS, MSYS2 gcc
+ OpenBLAS on Windows), so agreement here does not prove agreement for other stacks.

## iceforge

`iceforge` has a `pip` backend: if `import threedice` works in the Python that runs iceforge, `--backend pip` (and
`auto`) use `threedice.executable()`, and the pin check takes the commit from `threedice.ice_commit()`.
Order of `auto`: `$ICE_EXECUTABLE`, **pip**, native `3D-ICE-Emulator` on PATH, WSL `~/3d-ice-4.0`, docker.

Licence note: the wheel being GPL-3.0-or-later does not affect iceforge (MIT), which only runs the binary as a separate process.
