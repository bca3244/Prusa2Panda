"""Check the files the web smoke test (tools/smoke_web.js) downloaded.

    python tools/check_web_outputs.py <download dir>
"""

from __future__ import annotations

import json
import os
import sys
import zipfile


def check(case: dict) -> list:
    errs = []
    with zipfile.ZipFile(case["file"]) as z:
        names = set(z.namelist())
        for need in ("3D/3dmodel.model", "Metadata/model_settings.config", "Metadata/project_settings.config"):
            if need not in names:
                errs.append(f"missing {need}")
        if errs:
            return errs
        cfg = json.loads(z.read("Metadata/project_settings.config"))
    printer = case["printer"]
    diffs = cfg.get("different_settings_to_system") or []
    if printer == "__prusa__":
        if not cfg.get("printable_area") or str(cfg.get("printer_settings_id", "")).startswith("Bambu Lab"):
            errs.append("PrusaSlicer printer not carried as a custom printer")
        return errs
    if cfg.get("printer_settings_id") != printer:
        errs.append(f"printer {cfg.get('printer_settings_id')!r}, expected {printer!r}")
    if any(cfg.get("inherits_group") or []):
        errs.append(f"system presets should not inherit: {cfg.get('inherits_group')}")
    if not cfg.get("print_settings_id") or not all(cfg.get("filament_settings_id") or [""]):
        errs.append("process / filament preset names missing")
    want = case.get("expect_filaments")
    if want and cfg.get("filament_settings_id") != want:
        errs.append(f"filaments {cfg.get('filament_settings_id')}, expected {want}")
    if case.get("filament") and cfg.get("filament_settings_id") != [case["filament"]]:
        errs.append(f"filaments {cfg.get('filament_settings_id')}, expected [{case['filament']!r}]")
    if case["settings"] == "presets" and any(diffs):
        errs.append(f"name-only presets expected, got diffs {diffs}")
    if case["settings"] == "prusa" and not (diffs and diffs[0]):
        errs.append("PrusaSlicer process settings expected as process diffs")
    return errs


def main(argv) -> int:
    out = argv[1]
    with open(os.path.join(out, "cases.json"), encoding="utf-8") as f:
        cases = json.load(f)
    bad = 0
    for c in cases:
        errs = check(c)
        print(f"{c['name']}: {'OK' if not errs else '; '.join(errs)}")
        bad += bool(errs)
    if len(cases) < 5:
        print("expected 5 cases")
        bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
