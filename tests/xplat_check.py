"""Cross-platform numerical agreement check of the installed threedice wheel.

Runs the cases in tests/reference/cases/ with the installed binary and compares every output file with the files that the
Linux x86_64 wheel produced (tests/reference/expected/, recorded in tests/reference/reference.json).

    python xplat_check.py --check    [--threads 1,2,4] [--heavy] [--summary out.json] [--tol-abs 1e-6]
                                                                             exit 1 above the tolerance
    python xplat_check.py --generate [--heavy]                               (re)write the reference from THIS install
                                                                             (single thread; --heavy: only the heavy cases)

Threads. 3D-ICE 4953952 takes the thread count from the `numofcores N ;` line of the stack's solver section
(bison/stack_description_parser.y optional_numofcores, default 1; sources/thermal_data.c set_parallel_cores() calls
omp_set_num_threads(N), refused above omp_get_max_threads(); N is also the SuperLU_MT nprocs in system_matrix_build). For
--threads N>1 the stack is rewritten with `numofcores N ;` and OMP_NUM_THREADS=N is set so that N is honoured even on
runners with fewer cores. Every thread count is compared with the SINGLE-thread reference. Bit-identity across thread counts
is expected but is not a pass condition; only the tolerance is. --heavy adds tests/reference/heavy/* (larger, ~40 s).

Method. Outputs are text printed with limited digits, so differences are measured on the PARSED numbers: every
whitespace/comma separated token that parses as a float is compared (abs diff, rel diff = abs diff / |reference|, 0/0 -> 0),
all other tokens must be equal strings. "bit-identical" means the file bytes are equal after CRLF -> LF (the Windows C
runtime writes CRLF in text mode). A difference below the printed precision would be invisible here. The tolerance (default
1e-6 K absolute, fixed in advance) is applied to every numeric token of every file.
"""
import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(HERE, "reference")
CASES = os.path.join(REF, "cases")
HEAVY = os.path.join(REF, "heavy")
EXPECTED = os.path.join(REF, "expected")
MANIFEST = os.path.join(REF, "reference.json")
PIN = "4953952a1ef6d38807ff307212a6f15e5b2ef935"
TOKEN = re.compile(r"[\s,]+")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_norm(path):
    with open(path, "rb") as f:
        return f.read().replace(b"\r\n", b"\n")


def case_dirs(heavy_too=False, heavy_only=False):
    """{name: directory} of the light cases and/or the heavy ones."""
    out = {}
    for base, use in ((CASES, not heavy_only), (HEAVY, heavy_too or heavy_only)):
        if use and os.path.isdir(base):
            out.update({d: os.path.join(base, d) for d in sorted(os.listdir(base)) if os.path.isdir(os.path.join(base, d))})
    return out


NUMOFCORES = re.compile(r"^[ \t]*numofcores[^;]*;[ \t]*\n", re.M | re.I)
INITIAL_T = re.compile(r"^([ \t]*initial temperature[^;]*;[ \t]*\n)", re.M | re.I)


def with_threads(text, n):
    """The stack text with `numofcores n ;` (an existing numofcores line is removed first)."""
    text = NUMOFCORES.sub("", text)
    new, k = INITIAL_T.subn(lambda m: m.group(1) + f" numofcores {n} ;\n", text, count=1)
    assert k == 1, "no 'initial temperature' line to attach numofcores to"
    return new


def run_case(exe, name, src, work, threads=1):
    """Copy the case to a fresh directory, run the emulator with `threads` threads.
    Returns (rc, seconds, {output file: bytes}, log tail, threads the emulator reports using)."""
    dst = os.path.join(work, f"{name}_t{threads}")
    shutil.copytree(src, dst)
    stk = [f for f in os.listdir(dst) if f.endswith(".stk")]
    assert len(stk) == 1, (name, stk)
    if threads != 1:
        path = os.path.join(dst, stk[0])
        with open(path, "rb") as fh:
            text = fh.read().decode("latin-1").replace("\r\n", "\n")
        with open(path, "wb") as fh:
            fh.write(with_threads(text, threads).encode("latin-1"))
    before = set(os.listdir(dst))
    env = dict(os.environ, OMP_NUM_THREADS=str(threads))
    t = time.time()
    r = subprocess.run([exe, stk[0]], cwd=dst, capture_output=True, text=True, timeout=1800, env=env)
    dt = time.time() - t
    outs = {f: read_norm(os.path.join(dst, f)) for f in sorted(set(os.listdir(dst)) - before)}
    m = re.search(r"Used threads \((\d+)\)", r.stdout)
    return r.returncode, dt, outs, (r.stdout + r.stderr)[-500:], int(m.group(1)) if m else None


def _isnum(t):
    try:
        float(t)
        return True
    except ValueError:
        return False


def compare_bytes(ref, got):
    """Comparison of one output file on its parsed numbers. Returns a dict."""
    res = {"bit_identical": ref == got, "ref_sha256": sha(ref), "got_sha256": sha(got),
           "n_numbers": 0, "max_abs": 0.0, "max_rel": 0.0, "structure_ok": True}
    a = TOKEN.split(ref.decode("latin-1").strip())
    b = TOKEN.split(got.decode("latin-1").strip())
    if len(a) != len(b):
        res["structure_ok"] = False
        return res
    for x, y in zip(a, b):
        if _isnum(x) and _isnum(y):
            fx, fy = float(x), float(y)
            d = abs(fx - fy)
            res["n_numbers"] += 1
            if d > res["max_abs"] or math.isnan(d):
                res["max_abs"] = d
            if fx != 0.0:
                res["max_rel"] = max(res["max_rel"], d / abs(fx))
            elif d != 0.0:
                res["max_rel"] = float("inf")
        elif x != y:
            res["structure_ok"] = False
    return res


def input_hashes(d):
    return {f: sha(read_norm(os.path.join(d, f))) for f in sorted(os.listdir(d))}


def provenance(exe):
    import threedice
    info = threedice.build_info()
    with open(exe, "rb") as f:
        exe_sha = sha(f.read())
    return {"threedice_version": threedice.__version__, "ice_commit": threedice.ice_commit(),
            "platform": platform.platform(), "machine": platform.machine(), "system": platform.system(),
            "python": platform.python_version(), "binary_sha256": exe_sha,
            "build_date": info.get("build_date"), "toolchain": info.get("toolchain")}


def generate(exe, prov, work, heavy):
    if heavy and os.path.isfile(MANIFEST):          # add the heavy cases to the existing reference
        with open(MANIFEST) as f:
            manifest = json.load(f)
    else:
        shutil.rmtree(EXPECTED, ignore_errors=True)
        manifest = {"provenance": prov, "cases": {}}
    for name, src in case_dirs(heavy_only=heavy).items():
        rc, dt, outs, tail, _ = run_case(exe, name, src, work)
        if rc != 0:
            sys.exit(f"{name}: emulator exit {rc}\n{tail}")
        shutil.rmtree(os.path.join(EXPECTED, name), ignore_errors=True)
        os.makedirs(os.path.join(EXPECTED, name))
        for f, data in outs.items():
            with open(os.path.join(EXPECTED, name, f), "wb") as fh:
                fh.write(data)
        manifest["cases"][name] = {"inputs_sha256": input_hashes(src),
                                   "outputs_sha256": {f: sha(d) for f, d in outs.items()}}
        print(f"{name}: {len(outs)} files, {dt:.2f} s")
    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
        f.write("\n")


def check_case(exe, name, src, manifest, work, tol, threads):
    entry = {"files": {}, "ok": True, "threads": threads}
    if input_hashes(src) != manifest["cases"][name]["inputs_sha256"]:
        entry.update(ok=False, error="input files differ from the recorded sha256")
        return entry
    rc, dt, outs, tail, used = run_case(exe, name, src, work, threads)
    entry["seconds"] = round(dt, 3)
    entry["returncode"] = rc
    entry["used_threads"] = used
    if rc == 0 and used != threads:
        entry.update(ok=False, error=f"emulator reports {used} threads, requested {threads}")
        return entry
    if rc != 0:
        entry.update(ok=False, error=f"emulator exit {rc}: {tail}")
        return entry
    exp_dir = os.path.join(EXPECTED, name)
    exp_files = sorted(os.listdir(exp_dir))
    if sorted(outs) != exp_files:
        entry.update(ok=False, error=f"output files differ: got {sorted(outs)} expected {exp_files}")
        return entry
    for f in exp_files:
        with open(os.path.join(exp_dir, f), "rb") as fh:
            res = compare_bytes(fh.read(), outs[f])
        res["ok"] = res["structure_ok"] and res["max_abs"] <= tol
        entry["files"][f] = res
        if not res["ok"]:
            entry["ok"] = False
    entry["bit_identical"] = all(r["bit_identical"] for r in entry["files"].values())
    entry["max_abs"] = max(r["max_abs"] for r in entry["files"].values())
    entry["max_rel"] = max(r["max_rel"] for r in entry["files"].values())
    return entry


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--generate", action="store_true")
    g.add_argument("--check", action="store_true")
    ap.add_argument("--threads", default="1", help="comma separated thread counts to run (default 1)")
    ap.add_argument("--heavy", action="store_true", help="include tests/reference/heavy/* (with --generate: only those)")
    ap.add_argument("--summary", help="write the per-case/per-file results as JSON here")
    ap.add_argument("--tol-abs", type=float, default=1e-6)
    ap.add_argument("--label", default=os.environ.get("XPLAT_LABEL", platform.system() + "-" + platform.machine()))
    args = ap.parse_args()

    import threedice
    exe = threedice.executable()
    assert threedice.ice_commit() == PIN, threedice.ice_commit()
    prov = provenance(exe)
    work = tempfile.mkdtemp(prefix="xplat_")
    if args.generate:
        generate(exe, prov, work, args.heavy)
        return 0

    with open(MANIFEST) as f:
        manifest = json.load(f)
    threads_list = [int(x) for x in args.threads.split(",")]
    results = {}                                        # "<case>@t<threads>" -> entry
    for name, src in case_dirs(heavy_too=args.heavy).items():
        for n in threads_list:
            results[f"{name}@t{n}"] = check_case(exe, name, src, manifest, work, args.tol_abs, n)
    shutil.rmtree(work, ignore_errors=True)
    failed = [n for n, e in results.items() if not e["ok"]]
    done = [e for e in results.values() if "max_abs" in e]
    summary = {"label": args.label, "tolerance_abs": args.tol_abs, "threads": threads_list, "cpu_count": os.cpu_count(),
               "provenance": prov, "reference_provenance": manifest["provenance"],
               "all_bit_identical": len(done) == len(results) and all(e["bit_identical"] for e in done),
               "max_abs": max((e["max_abs"] for e in done), default=None),
               "max_rel": max((e["max_rel"] for e in done), default=None),
               "passed": not failed, "failed_cases": failed, "cases": results}
    if args.summary:
        with open(args.summary, "w") as f:
            json.dump(summary, f, indent=1, sort_keys=True)
            f.write("\n")
    for name, e in results.items():
        print(f"{args.label:16s} {name:34s} bit_identical={e.get('bit_identical')} max_abs={e.get('max_abs')} "
              f"max_rel={e.get('max_rel')} {e.get('error', '')}")
    # GitHub shows at most 10 notices per step, so the full table goes out as ONE notice
    rows = []
    for name, e in results.items():
        row = (f"{name}: bit_identical={e.get('bit_identical')} max_abs={e.get('max_abs')} max_rel={e.get('max_rel')} "
               f"ok={e['ok']} used_threads={e.get('used_threads')} t={e.get('seconds')}s {e.get('error', '')}")
        for f, r in e.get("files", {}).items():
            if not r["bit_identical"]:
                row += f"\n    {f}: max_abs={r['max_abs']} max_rel={r['max_rel']} n={r['n_numbers']} structure_ok={r['structure_ok']}"
        rows.append(row)
    body = f"cpus={os.cpu_count()}\n" + "\n".join(rows)
    print("::notice title=" + f"xplat {args.label} table" + "::" + body.replace("%", "%25").replace("\n", "%0A"))
    print(f"::notice title=xplat {args.label}::max abs diff vs Linux x86_64 reference = {summary['max_abs']} K, "
          f"max rel = {summary['max_rel']}, bit-identical on all files = {summary['all_bit_identical']}, "
          f"threads {threads_list}, cpus {os.cpu_count()}, tolerance {args.tol_abs} K, passed = {summary['passed']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
