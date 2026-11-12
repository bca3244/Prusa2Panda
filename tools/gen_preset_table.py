"""Generate the web version's preset tables from a slicer's bundled system profiles.

    python tools/gen_preset_table.py --slicer bambu --version 02.09.01.70 \\
        --profiles BambuStudio/resources/profiles --out _site/presets/bambu
    python tools/gen_preset_table.py --slicer orca --version 2.5.0 \\
        --profiles OrcaSlicer/resources/profiles --out _site/presets/orca

``--profiles`` is the slicer source tree's ``resources/profiles`` folder: the same vendor
JSON files the slicer installs into its ``system`` folder. The presets are resolved with
the desktop converter's own reader (bambu_install.BambuInstall), so the web version sees the
same values the desktop version reads from an installed slicer.

Writes ``index.json`` (slicer, version, vendors and their printers) and one
``<vendor>.json`` table per vendor with that vendor's printers and every process and
filament preset that fits one of them (see prusa2panda/preset_table.py).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from prusa2panda.bambu_install import BambuInstall  # noqa: E402
from prusa2panda.preset_table import TABLE_FORMAT, TABLE_KEYS, compatible  # noqa: E402


def _instantiable(cfg: dict) -> bool:
    return str(cfg.get("instantiation", "true")).lower() != "false"


def _subset(cfg: dict, kind: str) -> dict:
    return {k: cfg[k] for k in TABLE_KEYS[kind] if k in cfg}


def _safe_file_name(vendor: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in vendor) + ".json"


def generate(slicer: str, profiles: str, version: str, out_dir: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        os.symlink(os.path.abspath(profiles), os.path.join(tmp, "system"), target_is_directory=True)
        inst = BambuInstall(slicer, tmp)
        index = inst._load_system_index()  # noqa: SLF001 - the generator is the reader's friend
        resolved = {}
        for (kind, name), preset in index.items():
            if kind not in TABLE_KEYS:
                continue
            cfg = inst.resolve(kind, name)
            if cfg is None or not _instantiable(preset.data) or not _instantiable(cfg):
                continue
            resolved[(kind, name)] = (preset.vendor, cfg)

    vendors = sorted({v for (k, _n), (v, _c) in resolved.items() if k == "machine"})
    os.makedirs(out_dir, exist_ok=True)
    entries = []
    for vendor in vendors:
        printers = sorted(n for (k, n), (v, _c) in resolved.items() if k == "machine" and v == vendor)
        table = {"format": TABLE_FORMAT, "slicer": slicer, "version": version, "vendor": vendor,
                 "machine": {}, "process": {}, "filament": {}}
        for name in printers:
            table["machine"][name] = _subset(resolved[("machine", name)][1], "machine")
        for kind in ("process", "filament"):
            for (k, name), (_v, cfg) in sorted(resolved.items()):
                if k == kind and any(compatible(cfg, p) for p in printers):
                    sub = _subset(cfg, kind)
                    cp = [p for p in sub.get("compatible_printers") or [] if p in table["machine"]]
                    if sub.get("compatible_printers"):
                        sub["compatible_printers"] = cp  # only this vendor's printers matter here
                    table[kind][name] = sub
        fname = _safe_file_name(vendor)
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            json.dump(table, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        models = {}
        for name in printers:
            models.setdefault(str(table["machine"][name].get("printer_model") or ""), []).append(name)
        try:
            if vendor == "BBL":
                raise OSError  # its vendor file says "Bambulab"
            with open(os.path.join(profiles, vendor + ".json"), encoding="utf-8") as f:
                title = str(json.load(f).get("name") or vendor)
        except (OSError, ValueError):
            title = "Bambu Lab" if vendor == "BBL" else vendor
        entries.append({"name": vendor, "title": title, "file": fname, "printers": printers,
                        "models": {m: v for m, v in sorted(models.items())}})
    idx = {"format": TABLE_FORMAT, "slicer": slicer, "version": version, "vendors": entries}
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1, sort_keys=True)
    return idx


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--slicer", choices=("bambu", "orca"), required=True)
    ap.add_argument("--profiles", required=True, help="the slicer's resources/profiles folder")
    ap.add_argument("--version", required=True, help="the slicer release the profiles come from")
    ap.add_argument("--out", required=True, help="output folder")
    a = ap.parse_args(argv)
    idx = generate(a.slicer, a.profiles, a.version.lstrip("v"), a.out)
    n = sum(len(v["printers"]) for v in idx["vendors"])
    print(f"{a.slicer} {idx['version']}: {len(idx['vendors'])} vendor(s), {n} printers -> {a.out}")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
