"""Batch driver: cases -> rendered inputs -> cached / parallel 3D-ICE solves -> fields and probe temperatures."""
import concurrent.futures as cf
import datetime
import hashlib
import json
import os
import queue
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import warnings

import numpy as np

from .cache import Cache, case_key
from .floorplan import EDGE_POLICIES, Floorplan
from .grid import Grid
from .probes import Probes, map_probes, probe_values
from .stack import Stack, StackError, sha256
from .tmap import read_axis, read_tmap

FORMAT_VERSION = 1
STACK_FILE = "stack.stk"


class BatchError(RuntimeError):
    def __init__(self, failures):
        self.failures = failures                     # {case name: message}
        lines = [f"{len(failures)} case(s) failed:"] + [f"  {k}: {v}" for k, v in failures.items()]
        super().__init__("\n".join(lines))


class Case:
    """One solve: `floorplans` {die instance: Floorplan} replaces those dies' floorplans; `files` {name: text or bytes}
    adds or overrides input files (e.g. a placement's .lyt); `meta` is free-form JSON-able data kept in the output."""

    def __init__(self, name, floorplans=None, files=None, meta=None):
        self.name = str(name)
        self.floorplans = dict(floorplans or {})
        self.files = {k: (v.encode("utf-8") if isinstance(v, str) else bytes(v)) for k, v in (files or {}).items()}
        self.meta = meta or {}
        for k, v in self.floorplans.items():
            if not isinstance(v, Floorplan):
                raise TypeError(f"case {self.name}: floorplan of {k} must be a Floorplan")


def cases_product(stack: Stack, placements, power_maps):
    """Cases for every (placement, power map) pair.

    placements: {placement name: {die instance: (dx_um, dy_um)}}
    power_maps: {power map name: {die instance: Floorplan in die coordinates, or a 2-D array of watts per cell}}
    A die without an offset in a placement is not shifted. Case name: '<placement>__<power map>'."""
    out = []
    for pname, offsets in placements.items():
        for mname, pm in power_maps.items():
            fps = {}
            for inst, fp in pm.items():
                dx, dy = offsets.get(inst, (0.0, 0.0))
                if not isinstance(fp, Floorplan):
                    fp = Floorplan.from_power_grid(fp, stack.grid)
                fps[inst] = fp.shifted(float(dx), float(dy))
            out.append(Case(f"{pname}__{mname}", fps, meta={"placement": pname, "power_map": mname,
                                                            "offsets": {k: list(map(float, v)) for k, v in offsets.items()}}))
    return out


# ---------------------------------------------------------------------------------------------------------- solver
def resolve_command(executable=None):
    """Solver command as a list: `executable` (path or list), else $THREEDICE_EXECUTABLE, else the bundled binary."""
    if executable is None:
        env = os.environ.get("THREEDICE_EXECUTABLE")
        if env:
            return [a.strip('"') for a in shlex.split(env, posix=(os.name != "nt"))]
        import threedice
        return [threedice.executable()]
    if isinstance(executable, (list, tuple)):
        return [str(x) for x in executable]
    return [str(executable)]


def _file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def solver_identity(cmd):
    """SHA-256 of the solver binary (locally, or inside WSL for a `wsl ... <path>` command); else the command string."""
    if len(cmd) == 1 and os.path.isfile(cmd[0]):
        return "sha256:" + _file_sha(cmd[0])
    if os.path.basename(cmd[0]).lower() in ("wsl", "wsl.exe") and len(cmd) >= 2:
        try:
            r = subprocess.run([cmd[0], "-e", "sha256sum", cmd[-1]], capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and r.stdout.split():
                return "sha256:" + r.stdout.split()[0]
        except (OSError, subprocess.SubprocessError):
            pass
    warnings.warn(f"cannot hash the solver binary of {cmd!r}; the cache is keyed on the command string, so a rebuilt "
                  "binary at the same path would not invalidate it (pass solver_id= to fix)")
    return "cmd:" + " ".join(cmd)


def resolve_batch_command(executable=None):
    """3D-ICE-Batch command as a list: `executable` (path or list), else $THREEDICE_BATCH_EXECUTABLE, else the bundled
    binary (threedice.batch_executable())."""
    if executable is None:
        env = os.environ.get("THREEDICE_BATCH_EXECUTABLE")
        if env:
            return [a.strip('"') for a in shlex.split(env, posix=(os.name != "nt"))]
        import threedice
        return [threedice.batch_executable()]
    return resolve_command(executable)


class _NativeSession:
    """One 3D-ICE-Batch process: parses and factorises the stack once, then solves floorplan variants (protocol in
    native/ice_batch.cpp). Every reply is awaited with `timeout`; a dead or silent solver raises RuntimeError."""

    def __init__(self, cmd, directory, timeout):
        self.dir, self.timeout = directory, timeout
        env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
        env["WSLENV"] = ":".join(x for x in (env.get("WSLENV", ""), "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS") if x)
        self.proc = subprocess.Popen(list(cmd) + [STACK_FILE], cwd=directory, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.lines = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        ready = self._reply()
        if not ready.startswith("READY "):
            self.close()
            raise RuntimeError(f"3D-ICE-Batch did not start: {ready}")
        _, secs, rows, cols = ready.split()
        self.factor_seconds, self.shape = float(secs), (int(rows), int(cols))

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line.rstrip("\n"))
        self.lines.put(None)

    def _reply(self):
        try:
            line = self.lines.get(timeout=self.timeout)
        except queue.Empty:
            self.close()
            raise RuntimeError(f"3D-ICE-Batch gave no reply within {self.timeout} s")
        if line is None:
            raise RuntimeError(f"3D-ICE-Batch exited (code {self.proc.poll()})")
        return line

    def command(self, line):
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except OSError as e:
            raise RuntimeError(f"3D-ICE-Batch is not running: {e}")
        r = self._reply()
        if r.startswith("ERR"):
            raise RuntimeError(r[4:])
        return r

    def close(self):
        try:
            if self.proc.poll() is None:
                self.proc.stdin.write("QUIT\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=10)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        if self.proc.poll() is None:
            self.proc.kill()


def _solve_native(cmd, items, grid: Grid, elements, timeout, work_dir, keep_dir):
    """Solve cases that differ only in their floorplan files with one 3D-ICE-Batch process.

    items: [(key, case name, stack text, files, fp_files {instance: file name})]. Returns ({key: (fields, info)},
    {case name: error}). The session directory holds the shared files; each case's floorplans go to its own subdir."""
    d = tempfile.mkdtemp(prefix="threedice_native_", dir=work_dir)
    results, failures = {}, {}
    session = None
    try:
        _, _, text, files, _ = items[0]
        for name, data in files.items():
            p = os.path.join(d, _safe_name(name))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        with open(os.path.join(d, STACK_FILE), "w", encoding="latin-1", newline="\n") as f:
            f.write(text)
        try:
            session = _NativeSession(cmd, d, timeout)
        except Exception as e:                           # every case of the session fails with the same reason
            return {}, {name: str(e) for _, name, *_ in items}
        if session.shape != tuple(grid.shape):
            return {}, {name: f"3D-ICE-Batch grid {session.shape} != stack grid {tuple(grid.shape)}"
                        for _, name, *_ in items}
        for i, (key, name, _, case_files, fp_files) in enumerate(items):
            sub = f"c{i:05d}"
            try:
                os.makedirs(os.path.join(d, sub))
                for inst, fname in fp_files.items():
                    with open(os.path.join(d, sub, fname), "wb") as f:
                        f.write(case_files[fname])
                    session.command(f"FLP {inst} {sub}/{fname}")
                secs = float(session.command(f"SOLVE {sub}/ " + " ".join(elements)).split()[1])
                fields = {e: np.fromfile(os.path.join(d, sub, e + ".f64"), dtype="<f8").reshape(grid.shape)
                          for e in elements}
                for e, a in fields.items():
                    if not np.all(np.isfinite(a)):
                        raise RuntimeError(f"non-finite temperatures in {e}")
                results[key] = (fields, {"seconds": round(secs, 6), "backend": "native",
                                         "factor_seconds": round(session.factor_seconds, 4),
                                         "session_cases": len(items)})
            except Exception as e:
                failures[name] = str(e)
                if session.proc.poll() is not None:      # the solver died: the rest of this session cannot run
                    for _, rest, *_ in items[i + 1:]:
                        failures[rest] = f"3D-ICE-Batch exited before this case: {e}"
                    break
            finally:
                if not keep_dir:
                    shutil.rmtree(os.path.join(d, sub), ignore_errors=True)
        return results, failures
    finally:
        if session is not None:
            session.close()
        if not keep_dir:
            shutil.rmtree(d, ignore_errors=True)


def _safe_name(name):
    if os.path.isabs(name) or ".." in name.replace("\\", "/").split("/"):
        raise StackError(f"input file name {name!r} must be relative and stay inside the case directory")
    return name


def _solve(cmd, text, files, tmaps, grid: Grid, timeout, work_dir, keep_dir):
    d = tempfile.mkdtemp(prefix="threedice_batch_", dir=work_dir)
    try:
        for name, data in files.items():
            p = os.path.join(d, _safe_name(name))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        with open(os.path.join(d, STACK_FILE), "w", encoding="latin-1", newline="\n") as f:
            f.write(text)
        env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
        env["WSLENV"] = ":".join(x for x in (env.get("WSLENV", ""), "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS") if x)
        t0 = time.time()
        try:
            r = subprocess.run(list(cmd) + [STACK_FILE], cwd=d, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"solver timed out after {timeout} s")
        seconds = time.time() - t0
        tail = (r.stdout + r.stderr)[-800:]
        if r.returncode != 0:
            raise RuntimeError(f"solver exit {r.returncode}: {tail}")
        fields = {}
        for e, fname in tmaps.items():
            p = os.path.join(d, fname)
            if not os.path.isfile(p):
                raise RuntimeError(f"solver wrote no {fname}: {tail}")
            fields[e] = read_tmap(p, grid.shape)
        xa, ya = os.path.join(d, "xaxis.txt"), os.path.join(d, "yaxis.txt")
        if os.path.isfile(xa) and os.path.isfile(ya):
            grid.check_axes(read_axis(xa), read_axis(ya))
        return fields, {"seconds": round(seconds, 4), "stdout_tail": tail[-300:]}
    finally:
        if not keep_dir:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------------------------------------- result
class BatchResult:
    """Arrays of one batch (see docs/batch_api_design.md section 5). `data` holds every array by its .npz key."""

    def __init__(self, data):
        self.data = data
        self.meta = json.loads(str(data["meta_json"]))

    def __getitem__(self, key):
        return self.data[key]

    @property
    def case_names(self):
        return [str(x) for x in self.data["case_names"]]

    @property
    def probe_T(self):
        return self.data["probe_T"]

    def field(self, element):
        return self.data["field__" + element]

    def save(self, path, compress=False):
        tmp = path + ".tmp.npz"
        (np.savez_compressed if compress else np.savez)(tmp, **self.data)
        os.replace(tmp, path)
        return path


def load_batch(path):
    with np.load(path, allow_pickle=False) as z:
        return BatchResult({k: z[k] for k in z.files})


# ---------------------------------------------------------------------------------------------------------- driver
def run_batch(stack, cases, probes=None, outputs=None, out=None, cache_dir=None, workers=None, interp="cell",
              edge_policy="error", executable=None, solver_id=None, timeout=None, save_fields=True,
              field_dtype="float32", compress=False, work_dir=None, keep_dirs=False, backend="process"):
    """Solve every case (cached, `workers` solver processes at once) and return a BatchResult; also save it to `out`.

    stack: Stack or path of a .stk. cases: list of Case. probes: Probes or None. outputs: element names whose full field
    is kept (probe elements are always added; default: every die). interp: 'cell' or 'bilinear'. edge_policy: 'error',
    'snap' or 'allow' for generated floorplan blocks that end mid-cell.

    backend: 'process' runs 3D-ICE-Emulator once per case (temperatures as printed in its Tmap files, 3 decimals);
    'native' runs 3D-ICE-Batch, which factorises the stack once and solves every case that differs only in its
    floorplans with that factorisation (full double precision; steady state on a uniform grid only). With 'native',
    `executable` names the 3D-ICE-Batch binary (default $THREEDICE_BATCH_EXECUTABLE, else the bundled one)."""
    if backend not in ("process", "native"):
        raise ValueError("backend must be 'process' or 'native'")
    if not isinstance(stack, Stack):
        stack = Stack.from_file(stack)
    if edge_policy not in EDGE_POLICIES:
        raise ValueError(f"edge_policy must be one of {EDGE_POLICIES}")
    if field_dtype not in ("float32", "float64"):
        raise ValueError("field_dtype must be 'float32' or 'float64'")
    cases = list(cases)
    if not cases:
        raise ValueError("no cases")
    names = [c.name for c in cases]
    if len(set(names)) != len(names):
        raise ValueError("case names must be unique")
    grid = stack.grid
    probes = probes if probes is not None else Probes([], [], [])
    outputs = list(dict.fromkeys(list(probes.elements) + list(outputs if outputs is not None else
                                                               ([] if len(probes) else list(stack.dies)))))
    if not outputs:
        raise ValueError("nothing to output")
    index, weight, cell = map_probes(grid, probes.x, probes.y, interp)

    cmd = resolve_batch_command(executable) if backend == "native" else resolve_command(executable)
    sid = solver_id or solver_identity(cmd)
    if backend == "native" and not sid.startswith("native:"):
        sid = "native:" + sid                            # unrounded results: never mixed with process-backend entries
    cache = Cache(cache_dir) if cache_dir else None

    # 1. render every case first, so that input errors surface before any solve starts
    prepared = []
    for c in cases:
        fps = {inst: fp.aligned(grid, edge_policy) for inst, fp in c.floorplans.items()}
        fp_files = {inst: f"fp_{inst}.flp" for inst in fps}
        text, _ = stack.render(fp_files, outputs)          # validates instance and element names
        files = dict(stack.files)
        files.update({fp_files[i]: fps[i].render().encode("utf-8") for i in fps})
        files.update(c.files)
        masks = {inst: fps[inst].edge_cell_mask(grid) for inst in fps}
        prepared.append((c, fp_files, files, masks, case_key(sid, stack.render_body(fp_files), files)))
    fp_of_key = {key: fp_files for _, fp_files, _, _, key in prepared}

    # 2. cache lookup, then solve the distinct missing keys in parallel. An entry missing some requested element is
    #    re-solved for the union of its elements and the requested ones, so it only ever grows.
    fields_by_key, info_by_key, failures = {}, {}, {}
    todo = {}
    for c, fp_files, files, _, key in prepared:
        if key in fields_by_key or key in todo:
            continue
        hit = cache.load(key) if cache else None
        if hit is not None and set(outputs) <= set(hit[0]):
            fields_by_key[key], info_by_key[key] = hit[0], dict(hit[1], cached=True)
        else:
            elems = list(dict.fromkeys(outputs + sorted(hit[0] if hit else [])))
            text, tmaps = stack.render(fp_files, elems)
            todo[key] = (c.name, text, files, tmaps)
    if workers is None:
        workers = max(1, (os.cpu_count() or 2) // 2)
    workers = max(1, min(int(workers), len(todo) or 1))
    if backend == "native" and todo:
        # cases that differ only in their floorplan files share one factorisation: group by everything else
        groups = {}
        for key, (name, text, files, tmaps) in todo.items():
            fps = fp_of_key[key]
            shared = sorted((k, sha256(v)) for k, v in files.items() if k not in set(fps.values()))
            gkey = (stack.render_body(fps), tuple(shared))
            groups.setdefault(gkey, []).append((key, name, text, files, fps, tmaps))
        jobs = []
        for members in groups.values():
            elems = list(dict.fromkeys(e for m in members for e in m[5]))
            text = stack.render(members[0][4], elems)[0]
            n_chunks = max(1, min(workers, len(members)))
            for j in range(n_chunks):
                chunk = members[j::n_chunks]
                jobs.append(([(k, nm, text, fl, fp) for k, nm, _, fl, fp, _ in chunk], elems))
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_solve_native, cmd, items, grid, elems, timeout, work_dir, keep_dirs)
                    for items, elems in jobs]
            for fut in cf.as_completed(futs):
                res, fails = fut.result()
                failures.update(fails)
                for key, (fields, info) in res.items():
                    info.update(solver_id=sid, case=todo[key][0], cached=False)
                    if cache:
                        cache.store(key, fields, info)
                    fields_by_key[key], info_by_key[key] = fields, info
        todo = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_solve, cmd, text, files, tmaps, grid, timeout, work_dir, keep_dirs): (key, name)
                for key, (name, text, files, tmaps) in todo.items()}
        for fut in cf.as_completed(futs):
            key, name = futs[fut]
            try:
                fields, info = fut.result()
            except Exception as e:                       # recorded; raised together after all cases ran
                failures[name] = str(e)
                continue
            info.update(solver_id=sid, case=name, cached=False)
            if cache:
                cache.store(key, fields, info)
            fields_by_key[key], info_by_key[key] = fields, info
    if failures:
        raise BatchError(failures)

    # 3. assemble in case order
    n, p = len(cases), len(probes)
    probe_T = np.empty((n, p))
    edge = np.full((n, p), -1, np.int8)
    stacks = {e: np.empty((n,) + grid.shape, dtype=field_dtype) for e in outputs} if save_fields else {}
    for i, (c, _, _, masks, key) in enumerate(prepared):
        f = fields_by_key[key]
        for e in outputs:
            if save_fields:
                stacks[e][i] = f[e]
        for e in probes.elements:
            sel = np.array([pe == e for pe in probes.element])
            probe_T[i, sel] = probe_values(f[e], index[sel], weight[sel])
            if e in masks:
                edge[i, sel] = masks[e][cell[sel, 0], cell[sel, 1]].astype(np.int8)
    try:
        import threedice
        version = threedice.__version__
        commit = threedice.ice_commit() if executable is None and not os.environ.get("THREEDICE_EXECUTABLE") else None
    except Exception:
        version, commit = None, None
    meta = {"format_version": FORMAT_VERSION, "threedice_version": version, "ice_commit": commit, "solver_id": sid,
            "solver_command": cmd, "backend": backend, "interp": interp, "edge_policy": edge_policy, "stack_name": stack.name,
            "stack_sha256": stack.sha256, "outputs": outputs, "field_dtype": field_dtype if save_fields else None,
            "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "case_meta": {c.name: c.meta for c in cases},
            "solves": {c.name: info_by_key[k] for c, *_, k in prepared}}
    data = {"format_version": np.array(FORMAT_VERSION), "meta_json": np.array(json.dumps(meta, sort_keys=True)),
            "case_names": np.array(names, dtype=str), "cache_keys": np.array([k for *_, k in prepared], dtype=str),
            "outputs": np.array(outputs, dtype=str), "x_centers": grid.x_centers, "y_centers": grid.y_centers,
            "cell_size": np.array([grid.dx, grid.dy]),
            "probe_names": np.array(probes.names, dtype=str), "probe_element": np.array(probes.element, dtype=str),
            "probe_xy": probes.xy.reshape(-1, 2), "probe_cell": cell.reshape(-1, 2),
            "probe_index": index.reshape(-1, 4), "probe_weight": weight.reshape(-1, 4),
            "probe_T": probe_T, "probe_edge_cell": edge}
    for e, a in stacks.items():
        data["field__" + e] = a
    res = BatchResult(data)
    if out:
        res.save(str(out), compress=compress)
    return res
