"""Cross-platform numerical agreement check of the installed threedice wheel.

Runs the cases in tests/reference/cases/ with the installed binary and compares every output file with the files that the
Linux x86_64 wheel produced (tests/reference/expected/, recorded in tests/reference/reference.json).

    python xplat_check.py --check    [--summary out.json] [--tol-abs 1e-6]   exit 1 above the tolerance
    python xplat_check.py --generate                                         (re)write the reference from THIS install

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
EXPECTED = os.path.join(REF, "expected")
MANIFEST = os.path.join(REF, "reference.json")
PIN = "4953952a1ef6d38807ff307212a6f15e5b2ef935"
TOKEN = re.compile(r"[\s,]+")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_norm(path):
    with open(path, "rb") as f:
        return f.read().replace(b"\r\n", b"\n")


def case_names():
    return sorted(d for d in os.listdir(CASES) if os.path.isdir(os.path.join(CASES, d)))


def run_case(exe, name, work):
    """Copy the case to a fresh directory, run the emulator, return (rc, seconds, {output file: bytes}, log tail)."""
    src = os.path.join(CASES, name)
    dst = os.path.join(work, name)
    shutil.copytree(src, dst)
    stk = [f for f in os.listdir(dst) if f.endswith(".stk")]
    assert len(stk) == 1, (name, stk)
    before = set(os.listdir(dst))
    t = time.time()
    r = subprocess.run([exe, stk[0]], cwd=dst, capture_output=True, text=True, timeout=900)
    dt = time.time() - t
    outs = {f: read_norm(os.path.join(dst, f)) for f in sorted(set(os.listdir(dst)) - before)}
    return r.returncode, dt, outs, (r.stdout + r.stderr)[-500:]


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


def input_hashes(name):
    d = os.path.join(CASES, name)
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


def generate(exe, prov, work):
    shutil.rmtree(EXPECTED, ignore_errors=True)
    manifest = {"provenance": prov, "cases": {}}
    for name in case_names():
        rc, dt, outs, tail = run_case(exe, name, work)
        if rc != 0:
            sys.exit(f"{name}: emulator exit {rc}\n{tail}")
        os.makedirs(os.path.join(EXPECTED, name))
        for f, data in outs.items():
            with open(os.path.join(EXPECTED, name, f), "wb") as fh:
                fh.write(data)
        manifest["cases"][name] = {"inputs_sha256": input_hashes(name),
                                   "outputs_sha256": {f: sha(d) for f, d in outs.items()}}
        print(f"{name}: {len(outs)} files, {dt:.2f} s")
    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
        f.write("\n")


def check_case(exe, name, manifest, work, tol):
    entry = {"files": {}, "ok": True}
    if input_hashes(name) != manifest["cases"][name]["inputs_sha256"]:
        entry.update(ok=False, error="input files differ from the recorded sha256")
        return entry
    rc, dt, outs, tail = run_case(exe, name, work)
    entry["seconds"] = round(dt, 3)
    entry["returncode"] = rc
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
        generate(exe, prov, work)
        return 0

    with open(MANIFEST) as f:
        manifest = json.load(f)
    results = {name: check_case(exe, name, manifest, work, args.tol_abs) for name in case_names()}
    shutil.rmtree(work, ignore_errors=True)
    failed = [n for n, e in results.items() if not e["ok"]]
    done = [e for e in results.values() if "max_abs" in e]
    summary = {"label": args.label, "tolerance_abs": args.tol_abs, "provenance": prov,
               "reference_provenance": manifest["provenance"],
               "all_bit_identical": len(done) == len(results) and all(e["bit_identical"] for e in done),
               "max_abs": max((e["max_abs"] for e in done), default=None),
               "max_rel": max((e["max_rel"] for e in done), default=None),
               "passed": not failed, "failed_cases": failed, "cases": results}
    if args.summary:
        with open(args.summary, "w") as f:
            json.dump(summary, f, indent=1, sort_keys=True)
            f.write("\n")
    for name, e in results.items():
        print(f"{args.label:16s} {name:30s} bit_identical={e.get('bit_identical')} max_abs={e.get('max_abs')} "
              f"max_rel={e.get('max_rel')} {e.get('error', '')}")
        print(f"::notice title=xplat {args.label} {name}::bit_identical={e.get('bit_identical')} max_abs={e.get('max_abs')} "
              f"max_rel={e.get('max_rel')} ok={e['ok']} {e.get('error', '')}")
    print(f"::notice title=xplat {args.label}::max abs diff vs Linux x86_64 reference = {summary['max_abs']} K, "
          f"max rel = {summary['max_rel']}, bit-identical on all files = {summary['all_bit_identical']}, "
          f"tolerance {args.tol_abs} K, passed = {summary['passed']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
