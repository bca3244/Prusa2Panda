"""Assemble the web version (the GitHub Pages site) into one folder.

    python tools/build_web.py --out _site \\
        --bambu-profiles BambuStudio/resources/profiles --bambu-version 02.09.01.70 \\
        --orca-profiles OrcaSlicer/resources/profiles --orca-version 2.5.0

The site is the static page in ``web/``, the converter package as a zip that the page
unpacks into Pyodide, and the preset tables of each slicer (tools/gen_preset_table.py).
Serve it with any static web server, e.g. ``python -m http.server -d _site``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import zipfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

import gen_preset_table  # noqa: E402
from prusa2panda import __version__  # noqa: E402

# Desktop-only modules the browser never imports.
SKIP = {"gui.py", "__main__.py"}


def zip_package(path: str) -> None:
    pkg = os.path.join(ROOT, "prusa2panda")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in sorted(os.listdir(pkg)):
            if fn.endswith(".py") and fn not in SKIP:
                z.write(os.path.join(pkg, fn), "prusa2panda/" + fn)


def build(out: str, slicers: dict) -> dict:
    if os.path.isdir(out):
        shutil.rmtree(out)
    shutil.copytree(os.path.join(ROOT, "web"), out)
    os.makedirs(os.path.join(out, "py"), exist_ok=True)
    zip_package(os.path.join(out, "py", "prusa2panda.zip"))
    info = {"version": __version__, "slicers": {}}
    for slicer, (profiles, version) in slicers.items():
        idx = gen_preset_table.generate(slicer, profiles, version.lstrip("v"),
                                        os.path.join(out, "presets", slicer))
        info["slicers"][slicer] = {"version": idx["version"],
                                   "printers": sum(len(v["printers"]) for v in idx["vendors"])}
    with open(os.path.join(out, "build-info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)
    # GitHub Pages: serve the files as they are (no Jekyll).
    open(os.path.join(out, ".nojekyll"), "w").close()
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="_site")
    for s in ("bambu", "orca"):
        ap.add_argument(f"--{s}-profiles", help=f"the {s} source tree's resources/profiles folder")
        ap.add_argument(f"--{s}-version", help=f"the {s} release those profiles come from")
    a = ap.parse_args(argv)
    slicers = {}
    for s in ("bambu", "orca"):
        prof, ver = getattr(a, f"{s}_profiles"), getattr(a, f"{s}_version")
        if prof:
            if not ver:
                ap.error(f"--{s}-profiles needs --{s}-version")
            slicers[s] = (prof, ver)
    if not slicers:
        ap.error("give the profiles of at least one slicer")
    info = build(a.out, slicers)
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
