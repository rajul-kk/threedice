# Batch probe API: design (`threedice.batch`)

Status: design for the first version, written before the code. Date 2026-11-05.
Purpose: run many 3D-ICE solves (placements x power maps) and export (a) temperatures at given probe coordinates and
(b) the full temperature field of chosen stack elements, as ground truth for the sparse-sensor thermal reconstruction
work in the Thermo repository (plan item 3). Thermo is a consumer only; nothing here changes or depends on Thermo code.

## 1. Scope

In scope (v1):

* Steady solves and the final state of transient solves (`Tmap ( NAME, "file", final )`).
* Uniform grids only: `cell length a, width b ;` with the chip length and width whole multiples of the cell size.
* Floorplans generated per case from block lists or from per-cell power arrays; any other input file (for example a
  `.lyt` layout of a placement) supplied as raw text per case.
* Probes given in physical units (micrometres, the unit of 3D-ICE stack files), on a named stack element (die or layer).
* Output in one `.npz` file per batch; per-case solve cache on disk.

Out of scope (v1), refused with a clear error rather than half-supported:

* Microchannel / pin-fin stacks (`channel`, `microchannel`, `pinfin`): their wall cells have different widths, so the
  grid is not uniform.
* Non-uniform grids (`non-uniform`, `discretization`): 3D-ICE prints a different Tmap format for them.
* Power traces with more than one value per block (time-varying transient power), time series of Tmaps (`step`/`slot`
  outputs), Tflp/Tcell outputs. These are listed under open questions.

## 2. Architecture

```
 user code (e.g. Thermo)                       threedice.batch                                3D-ICE
 -----------------------        ------------------------------------------------        -----------------
  Stack.from_file(pkg.stk) ---> stack.py    parse dimensions -> Grid (grid.py)
  cases (placement x power) --> floorplan.py  Block / Floorplan, grid alignment check,
                                             snapping, per-cell power -> blocks
  Probes(die, x_um, y_um) ----> probes.py   probe -> (cell indices, weights)  [pure numpy]
                                     |
  run_batch(stack, cases, probes, outputs, out="b.npz", cache_dir=..., workers=N)
                                     |  for each case (thread pool, N concurrent solver processes)
                                     v
                                stack.render(case): rewrite floorplan paths,
                                replace the output section by Tmap(..., final),
                                force `numofcores 1`
                                     |
                                cache.py  key = sha256(format version, solver id,
                                          rendered stack, every input file, outputs)
                                     |-- hit  --> load fields from cache/<k[:2]>/<k>.npz
                                     |-- miss --> runner.py: temp dir, write files,   ---> 3D-ICE-Emulator stack.stk
                                     |            run solver, OMP_NUM_THREADS=1       <--- tmap_<NAME>.txt, xaxis.txt,
                                     |            tmap.py: parse + check against Grid      yaxis.txt
                                     |            store atomically in cache
                                     v
                                fields[case][NAME] (ny, nx) float64
                                     |
                                probes.apply(weights, fields) -> probe_T (n_cases, n_probes)
                                     v
                                BatchResult  --save-->  b.npz   (load_batch(path) reads it back)
```

Module layout (new subpackage, nothing else in the package changes behaviour):

| module | content | depends on |
|---|---|---|
| `threedice/batch/__init__.py` | public names | the modules below |
| `threedice/batch/grid.py` | `Grid` (uniform cell grid: chip size, cell size, centres, edges, cell lookup, alignment test) | numpy |
| `threedice/batch/floorplan.py` | `Block`, `Floorplan`, `.render()`, `Floorplan.from_power_grid()`, `align()` (error / snap / allow), block-edge cell mask | grid |
| `threedice/batch/stack.py` | `Stack` (text + referenced files), comment stripping, unsupported-feature check, `Grid` from `dimensions:`, case rendering | grid |
| `threedice/batch/probes.py` | `Probes`, `map_probes()` (cell / bilinear) -> index and weight arrays, `probe_values()` | grid |
| `threedice/batch/tmap.py` | parse Tmap and axis files | numpy |
| `threedice/batch/cache.py` | cache key, atomic store / load | numpy, hashlib |
| `threedice/batch/runner.py` | `Case`, `cases_product()`, `run_batch()`, solver command, `BatchResult`, `load_batch()` | all above |

Separation from the packaging and from upstream: the batch code only *runs* the bundled binary as a separate process,
like the `3d-ice-emulator` console script. `patches/`, `build_support.py`, `setup.py`, `upstream.json` and the binary
are untouched. `pyproject.toml` changes in two places only: the package list gains `threedice.batch`, and a new optional
extra `batch = ["numpy>=1.20"]`. `import threedice` keeps working without NumPy; only `import threedice.batch` needs it.
The wheel's test step gains NumPy (`CIBW_TEST_REQUIRES`) so the batch tests also run against every built wheel; the
tests skip cleanly where NumPy or the binary is missing.

## 3. Inputs

### 3.1 Stack

`Stack.from_file("pkg.stk")` reads the stack file and every file it references by a quoted path that exists next to it
(floorplans, layouts). `Stack(text, files={name: bytes})` builds one in memory. The stack holds the fixed part of the
problem: materials, heat sinks, dimensions, die definitions, stack order, solver mode.

Per case, the stack text is rendered by three rewrites, all on the comment-stripped text:

1. `die INST DIE floorplan "old"` -> `floorplan "fp_INST.flp"` for every instance whose floorplan the case supplies.
2. The `output :` section (always last in the 3D-ICE grammar) is replaced by `Tmap ( NAME, "tmap_NAME.txt", final ) ;`
   for each requested output. The batch controls file names, so parsing never depends on user output names.
3. Any `numofcores` line is removed and `numofcores 1 ;` added (see 3.5 and 7).

Rejected alternative: a templating language (Jinja placeholders in the .stk). It would make every stack batch-specific
and is not needed: placements and power maps live in floorplans and layouts, which the case supplies whole.

### 3.2 Cases: placements x power maps

A `Case` is a name, `floorplans: {instance: Floorplan}` and `files: {name: text|bytes}` (extra or overriding input
files, e.g. a placement's `.lyt`). The batch is a list of cases; nothing forces a product structure.
`cases_product(placements, power_maps)` builds the common product: a placement is `{instance: (dx_um, dy_um)}`, a
power map is `{instance: Floorplan or 2-D array of watts per cell}` in the die's own coordinates; the product shifts
every block by the placement offset. Case names are `"<placement>__<power map>"`.

`Floorplan.from_power_grid(P, grid, origin)` turns an `(ny, nx)` array of watts per cell into one block per non-zero
cell (all-zero arrays keep one zero block, since 3D-ICE needs a non-empty floorplan). Such floorplans are grid-aligned
by construction.

### 3.3 Probes

`Probes(element, x_um, y_um, names=None)`: arrays of equal length; `element` is the stack element (die or layer
instance) whose Tmap the probe reads; x along the chip *length*, y along the chip *width*, origin at the chip's
corner, in micrometres, matching 3D-ICE's floorplan coordinates. Every probe element is added to the outputs.

## 4. Probe-to-grid mapping

Facts checked on the binary of this package (pinned build, run through WSL, 2026-11-05): a Tmap file has one row per
cell along the width (y) and one column per cell along the length (x); row 0 is y = 0 (a 250 um block at the origin
heats the first value of the first row); `xaxis.txt` / `yaxis.txt` hold the cell centres with two decimals. With
`chip length 1100` and `cell length 250` 3D-ICE silently makes 4 columns and drops the last 100 um, so the batch refuses
a chip size that is not a whole multiple of the cell size.

Two modes, chosen per batch (`interp=`), both stored as `(n_probes, 4)` flat indices and weights so any user can
recompute probe values from the saved field:

* **`"cell"` (default)**: the value of the cell containing the probe. Cell index `floor(x / dx)`; a point on a
  shared cell boundary goes to the higher-index cell; `x == chip length` goes to the last cell. Reason for the default:
  3D-ICE's unknown *is* the cell temperature (finite-volume node); this mode returns exactly a number that is in the
  full field, so sparse-sensor ground truth and the field ground truth are consistent by construction, and a sensor
  smaller than a cell physically sits in one cell.
* **`"bilinear"`**: bilinear interpolation between the four surrounding cell centres. Useful when probe positions vary
  continuously (placement optimisation) and a piecewise-constant response is unwanted. Inside the half-cell band
  between the chip edge and the outermost centres there is no outer neighbour, so the value is held constant along that
  axis (clamped). Extrapolating linearly was rejected: it can overshoot at hot edges and the slope there is unknown.
* Rejected: higher-order (bicubic / spline) interpolation, since it invents sub-cell structure that the FV solution does
  not contain and can overshoot; and nearest-cell-centre mode, which equals `"cell"` on a uniform grid.

Points outside `[0, length] x [0, width]` raise an error (no silent clamping of mistakes).

**Die edges and partial cells.** Thermo found (report section 9.32) that a chiplet edge falling mid-cell gives the
partly covered cell its area share of the power while the layout assigns the material by cell centre, producing a row
of heated insulator cells and peak errors up to 14 K; the fix was snapping placements to whole cells. Here:

* Every generated floorplan block is checked against the grid before solving (`edge_policy=`):
  `"error"` (default) refuses misaligned blocks and names them; `"snap"` rounds each block edge to the nearest grid
  line (a block keeps its total power, its area can change by under one cell per side; a block that collapses or blocks
  that then overlap are an error); `"allow"` solves as given (for studying the artefact itself).
  Thermo's sampler snaps the *offset* and keeps the size; snapping edges is the equivalent for sizes that are already
  whole cells and also handles sizes that are not.
* The output carries `probe_edge_cell` (n_cases, n_probes; int8): 1 if the probe's cell is crossed by a block edge of
  that case's floorplan for that element, 0 if not, -1 if unknown (element whose floorplan the batch did not generate,
  or a layer). With aligned floorplans it is always 0; with `"allow"` it marks probes whose value contains the artefact.
* Not checked: raw `.lyt` files supplied in `Case.files` (their format is not parsed by v1). Open question 4.

## 5. Output format

One `.npz` (NumPy zip archive, written uncompressed by default, `compress=True` optional) per batch:

| key | shape / type | meaning |
|---|---|---|
| `format_version` | scalar int | 1 |
| `meta_json` | scalar str | JSON: threedice version, ice commit (when known), solver id, interp, edge policy, stack sha256, outputs, created (UTC) |
| `case_names` | (N,) str | |
| `cache_keys` | (N,) str | per-case solve key |
| `outputs` | (E,) str | stack elements with a field |
| `x_centers`, `y_centers` | (nx,), (ny,) float64 | cell centres, um |
| `cell_size` | (2,) float64 | (dx, dy) um |
| `field__<ELEMENT>` | (N, ny, nx) float32 (default) or float64 | full temperature field, K; omitted with `save_fields=False` |
| `probe_names`, `probe_element` | (P,) str | |
| `probe_xy` | (P, 2) float64 | um |
| `probe_cell` | (P, 2) int64 | (row, col) of the containing cell |
| `probe_index`, `probe_weight` | (P, 4) int64 / float64 | flat index into `ny*nx` and weight |
| `probe_T` | (N, P) float64 | probe temperatures, K |
| `probe_edge_cell` | (N, P) int8 | see section 4 |

Why `.npz`: NumPy is the only dependency the batch adds and the consumer (Thermo) already stores its data as `.npz`;
it loads with `np.load` without threedice installed; it holds named arrays plus a JSON metadata string. HDF5 (h5py)
was rejected: a compiled dependency with its own wheels and library versions for a gain (partial reads, appends) that
batches of this size (hundreds to a few thousand fields of at most ~250 x 250) do not need. Parquet / Zarr: same
objection, plus a field is naturally an array, not a table. CSV: loses shape, slow, large.

Field dtype: 3D-ICE prints temperatures with three decimals. float32 has a spacing of 3e-5 K near 300-500 K, so the
printed value is recovered exactly by rounding to 3 decimals; it halves the size (1000 cases x 2 elements x 248 x 250 is
0.5 GB in float32). Probe values are computed from the float64 parse, so in `"cell"` mode `probe_T` equals the
float64 field exactly and the float32 field to within 3.1e-5 K. `field_dtype="float64"` is available.

Precision limit: everything is limited to the 0.001 K printed by 3D-ICE's Tmap.

## 6. Caching

Key = SHA-256 of a canonical JSON of: cache format version; solver id; the rendered stack text *without* its output
section; the name and SHA-256 of every file written to the case directory. The probe set, the interpolation mode and
the output elements are *not* in the key: probes are recomputed from cached fields, so new probes never trigger new
solves; an entry stores the fields of every element solved so far, and a request for an element it lacks re-solves
the case once for the union of old and new elements and replaces the entry (entries only grow). (First draft had the
output list in the key; changed while writing the tests, because asking for one more element then re-solved every case
and kept two entries for the same physics.)

Solver id: SHA-256 of the executable file when it is a local file; for a `wsl <path>` command, the SHA-256 computed by
`sha256sum` inside WSL; otherwise the command string (with a warning that a rebuilt binary at the same path would not
invalidate the cache). `solver_id=` overrides it.

Store: `cache_dir/<key[:2]>/<key>.npz` with the float64 fields, the axes and a small metadata record (seconds, solver
id, stdout tail). Writes go to a temporary file in the same directory and are moved into place with `os.replace`, so a
crash or a parallel writer never leaves a half-written entry. An entry that fails to load is treated as a miss and
rewritten. No eviction: the cache is a directory the user deletes. `cache_dir=None` disables caching.

Rejected: keying on the user's case objects (pickling is not stable across versions) or on case names (names repeat
across experiments with different contents); a single database file (needs locking across workers).

## 7. Parallelism and reproducibility

Each solve is a separate `3D-ICE-Emulator` process with `numofcores 1` and `OMP_NUM_THREADS=1`. Parallelism is across
cases: a `ThreadPoolExecutor` with `workers` threads, each of which writes the inputs, launches one solver process and
waits for it, so up to `workers` solver processes run at once on the CPU.

* Threads, not a `ProcessPoolExecutor`, drive the solver processes: the solver is already a separate OS process, the
  Python side per case is writing a few small files and parsing one or a few text maps (milliseconds against a solve of
  0.2 s to tens of seconds), and a process pool would add Windows spawn start-up, pickling of fields, and import of the
  caller's `__main__` in every worker. If parsing ever dominates, the parse can move into a process pool without
  changing the API.
* `numofcores 1`: README "Threads" records that multi-threaded SuperLU_MT solves of this build are not reproducible (rare
  differences up to 3.5 K); single-thread solves have always been bit-identical across platforms. Case-level parallelism
  also scales better than intra-solve threading for many small solves.
* Default `workers = max(1, os.cpu_count() // 2)`. Memory per solve grows with the node count (a few MB for 10^3 nodes,
  hundreds of MB for 10^5-10^6); on a 16 GB machine large stacks need fewer workers. The user sets it.
* Results are assembled in case order, independent of completion order.

Failures: a case whose solver exits non-zero, times out (`timeout=` seconds per solve) or prints a map of the wrong
shape is recorded; after all cases run, `run_batch` raises `BatchError` listing every failed case with the tail of the
solver output. Successful cases are already cached, so a rerun after a fix only solves the failed ones.

## 8. Solver command

`executable=` accepts a path or a command list; default: `$THREEDICE_EXECUTABLE` (split with `shlex`) if set, else
`threedice.executable()`. A `wsl /home/.../3D-ICE-Emulator` prefix works from Windows because `wsl.exe` starts in the
current Windows directory and all paths in the rendered stack are relative to the case directory.

## 9. Limits

* Steady and final-state transient only; one power value per block; uniform grids; no channels.
* Values are limited to the printed 0.001 K; axes to 0.01 um (checked against the stack-derived grid with 0.006 um
  tolerance).
* All outputs of a batch share one grid (3D-ICE uses one in-plane grid per stack).
* `.lyt` contents are not checked for alignment.
* The cache trusts the solver id; it does not detect a changed shared library (OpenBLAS) behind the same binary.

## 10. Open questions

1. Time series (transient `step`/`slot` Tmaps) and per-block power traces: needed for Thermo? The file layout would gain
   a time axis (`field__X` (N, T, ny, nx)).
2. Should probes support averaging over a sensor footprint (area-weighted over cells) instead of a point? Easy to add as
   a third mode with more than four weights; the `(P, K)` index/weight layout already allows it.
3. Probe z (a probe between two layers): currently a probe belongs to one element's Tmap; vertical interpolation is not
   offered.
4. Parsing `.lyt` files to run the same edge check on layouts.
5. Non-uniform grids: 3D-ICE 4.0 prints a list of cells for them; supporting it means a different probe mapping
   (point-in-rectangle search).
