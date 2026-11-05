"""On-disk cache of solved fields, keyed by the SHA-256 of everything that determines a solve."""
import hashlib
import json
import os
import tempfile

import numpy as np

CACHE_FORMAT = 1


def case_key(solver_id, stack_body, files):
    """`stack_body`: the rendered stack without its output section (the output elements are not part of the key: an
    entry holds the fields of every element solved so far). `files`: {name: bytes} of every input file written to the
    case directory (the stack itself excluded)."""
    rec = {"cache_format": CACHE_FORMAT, "solver_id": solver_id,
           "stack_sha256": hashlib.sha256(stack_body.encode("utf-8")).hexdigest(),
           "files": {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())}}
    return hashlib.sha256(json.dumps(rec, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class Cache:
    def __init__(self, root):
        self.root = os.path.abspath(root)

    def path(self, key):
        return os.path.join(self.root, key[:2], key + ".npz")

    def load(self, key):
        """({element: field float64}, meta) or None when missing or unreadable (treated as a miss)."""
        p = self.path(key)
        if not os.path.isfile(p):
            return None
        try:
            with np.load(p, allow_pickle=False) as z:
                fields = {k[len("field__"):]: np.array(z[k], dtype=np.float64) for k in z.files if k.startswith("field__")}
                meta = json.loads(str(z["meta_json"]))
        except Exception:
            return None
        return fields, meta

    def store(self, key, fields, meta):
        """Atomic: write a temporary file in the same directory, then os.replace."""
        d = os.path.dirname(self.path(key))
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".npz", dir=d)
        try:
            with os.fdopen(fd, "wb") as f:
                np.savez(f, meta_json=np.array(json.dumps(meta, sort_keys=True)),
                         **{"field__" + e: np.asarray(v, dtype=np.float64) for e, v in fields.items()})
            os.replace(tmp, self.path(key))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
