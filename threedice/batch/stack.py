"""The fixed part of a batch: a 3D-ICE stack description plus the files it references, and its per-case rendering."""
import hashlib
import os
import re

from .grid import Grid

_NUM = r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)"
_CHIP = re.compile(r"\bchip\s+length\s+" + _NUM + r"\s*,\s*width\s+" + _NUM + r"\s*;")
_CELL = re.compile(r"\bcell\s+length\s+" + _NUM + r"\s*,\s*width\s+" + _NUM + r"\s*;")
_DIE_INST = re.compile(r"\bdie\s+(\w+)\s+(\w+)\s+floorplan\s+\"([^\"]*)\"\s*;")
_LAYER_INST = re.compile(r"\blayer\s+(\w+)\s+(\w+)\s*;")
_STACK_SEC = re.compile(r"\bstack\s*:(.*?)(?=\bsolver\s*:|\Z)", re.S)
_OUTPUT_SEC = re.compile(r"\boutput\s*:")
_NUMOFCORES = re.compile(r"\bnumofcores\s+\d+\s*;")
_INITIAL_T = re.compile(r"(\binitial\s+temperature\s+[^;]*;)")
_OUTPUT_MARK = "\n\noutput :\n"
_QUOTED = re.compile(r"\"([^\"]+)\"")
_UNSUPPORTED = {
    r"\bchannel\b": "microchannel/channel cavities (non-uniform wall cells)",
    r"\bmicrochannel\b": "microchannels (non-uniform wall cells)",
    r"\bpinfin\b": "pin-fin cavities (non-uniform wall cells)",
    r"\bnon-uniform\b": "non-uniform grids (different Tmap format)",
    r"\bdiscretization\b": "non-uniform discretization",
}


class StackError(ValueError):
    pass


def strip_comments(text):
    """Remove the C-style comments the 3D-ICE scanner accepts (`// ...` and `/* ... */`)."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def sha256(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


class Stack:
    """A stack file's text and the input files it references ({relative name: bytes})."""

    def __init__(self, text, files=None, name="stack"):
        self.text = strip_comments(text.replace("\r\n", "\n"))
        self.files = {k: (v.encode("utf-8") if isinstance(v, str) else bytes(v)) for k, v in (files or {}).items()}
        self.name = name
        for pat, what in _UNSUPPORTED.items():
            if re.search(pat, self.text):
                raise StackError(f"{what} are not supported by threedice.batch v1")
        if not re.search(r"\bsolver\s*:", self.text):
            raise StackError("no solver section found: is this a 3D-ICE stack file?")
        self.grid = self._grid()
        self.dies = {m.group(1): m.group(3) for m in _DIE_INST.finditer(self._stack_section())}
        self.layers = [m.group(1) for m in _LAYER_INST.finditer(self._stack_section())]

    @classmethod
    def from_file(cls, path):
        """Read a .stk and every file it references (by a quoted relative path outside the output section)."""
        with open(path, "rb") as f:
            text = f.read().decode("latin-1")
        base = os.path.dirname(os.path.abspath(path))
        body = strip_comments(text)
        m = _OUTPUT_SEC.search(body)
        if m:
            body = body[:m.start()]
        files = {}
        for ref in dict.fromkeys(_QUOTED.findall(body)):
            p = os.path.join(base, ref)
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    files[ref] = f.read()
        return cls(text, files, name=os.path.basename(path))

    def _stack_section(self):
        m = _STACK_SEC.search(self.text)
        if not m:
            raise StackError("no 'stack :' section")
        return m.group(1)

    def _grid(self):
        c, k = _CHIP.search(self.text), _CELL.search(self.text)
        if not c or not k:
            raise StackError("the dimensions section needs 'chip length L, width W ;' and 'cell length a, width b ;'")
        return Grid(float(c.group(1)), float(c.group(2)), float(k.group(1)), float(k.group(2)))

    @property
    def elements(self):
        return list(self.dies) + self.layers

    @property
    def sha256(self):
        h = hashlib.sha256(self.text.encode("utf-8"))
        for k in sorted(self.files):
            h.update(k.encode("utf-8") + b"\0" + sha256(self.files[k]).encode())
        return h.hexdigest()

    def render_body(self, floorplans):
        """The rendered stack without its output section: what determines the solution (used for the cache key)."""
        return self.render(floorplans, [])[0].split(_OUTPUT_MARK)[0]

    def render(self, floorplans, outputs):
        """Stack text for one case. `floorplans`: {die instance: floorplan file name}; `outputs`: element names.
        Returns (text, {element: Tmap file name})."""
        for inst in floorplans:
            if inst not in self.dies:
                raise StackError(f"die instance {inst!r} is not in the stack (dies: {', '.join(self.dies)})")
        for e in outputs:
            if e not in self.elements:
                raise StackError(f"output element {e!r} is not in the stack (elements: {', '.join(self.elements)})")

        def die_sub(m):
            if m.group(1) in floorplans:
                return f"die {m.group(1)} {m.group(2)} floorplan \"{floorplans[m.group(1)]}\" ;"
            return m.group(0)

        text = _DIE_INST.sub(die_sub, self.text)
        m = _OUTPUT_SEC.search(text)
        if m:
            text = text[:m.start()]
        text = _NUMOFCORES.sub("", text)
        text, n = _INITIAL_T.subn(lambda mm: mm.group(1) + "\n  numofcores 1 ;", text, count=1)
        if n != 1:
            raise StackError("no 'initial temperature' line in the solver section")
        tmaps = {e: f"tmap_{e}.txt" for e in outputs}
        text = text.rstrip() + _OUTPUT_MARK + "".join(f"  Tmap ( {e}, \"{f}\", final ) ;\n" for e, f in tmaps.items())
        return text, tmaps
