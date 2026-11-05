"""A stand-in for 3D-ICE-Emulator used by the batch tests that need no real solver.

Reads the rendered stack, writes `final` Tmaps in 3D-ICE's text layout (rows along the width, 3 decimals) plus
xaxis.txt / yaxis.txt. The field depends on the floorplan powers so that different cases give different fields.
Each run creates one file in the directory $FAKE_3DICE_LOG (for counting solves; concurrent appends to one file
are not atomic on Windows). A block named FAIL makes it exit 3.
"""
import os
import re
import sys
import uuid


def main(stk):
    text = open(stk).read()
    L, W = map(float, re.search(r"chip\s+length\s+([\d.]+)\s*,\s*width\s+([\d.]+)", text).groups())
    dx, dy = map(float, re.search(r"cell\s+length\s+([\d.]+)\s*,\s*width\s+([\d.]+)", text).groups())
    nx, ny = int(round(L / dx)), int(round(W / dy))
    fps = dict(re.findall(r"die\s+(\w+)\s+\w+\s+floorplan\s+\"([^\"]+)\"", text))
    if os.environ.get("FAKE_3DICE_LOG"):
        with open(os.path.join(os.environ["FAKE_3DICE_LOG"], uuid.uuid4().hex), "w") as f:
            f.write(os.getcwd())
    for k, (elem, out) in enumerate(re.findall(r"Tmap\s*\(\s*(\w+)\s*,\s*\"([^\"]+)\"\s*,\s*final\s*\)", text)):
        power = 0.0
        if elem in fps:
            flp = open(fps[elem]).read()
            if re.search(r"^FAIL\s*:", flp, re.M):
                sys.exit(3)
            power = sum(float(v) for v in re.findall(r"power values\s+([-\d.eE+]+)", flp))
        with open(out, "w") as f:
            f.write(f"% Thermal map for layer {elem}\n")
            for r in range(ny):
                f.write("  ".join(f"{300 + 0.5 * r + 0.25 * c + 10 * power + 0.0013 * k + 1e-4 * (r * c % 7):.3f}"
                                  for c in range(nx)) + "  \n")
            f.write("\n")
    with open("xaxis.txt", "w") as f:
        f.write("".join(f"{(c + 0.5) * dx:.2f}\n" for c in range(nx)))
    with open("yaxis.txt", "w") as f:
        f.write("".join(f"{(r + 0.5) * dy:.2f}\n" for r in range(ny)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
