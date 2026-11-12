"""The Python side of the web version (runs in the browser under Pyodide).

The page passes plain JSON in and gets plain JSON back, so the JavaScript side never touches
Python objects. Files live in Pyodide's in-memory file system: the page writes the uploaded
project and the preset tables there and reads the converted files back.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from . import __version__
from .bambu_install import SLICER_BAMBU, SLICER_ORCA, Selection
from .convert import (
    BEDS_PLATES,
    BEDS_SPLIT,
    PRINTER_PRUSA,
    convert_file,
    describe,
    filament_label,
    suggest_filament_map,
    used_extruders,
)
from .preset_table import TableInstall
from .target import load_config_file, normalized_type


def version() -> str:
    return __version__


def inspect(path: str, beds: Optional[List[int]] = None) -> str:
    """What the page shows after a file is picked, and the extruders the chosen beds use."""
    project = describe(path)
    used = used_extruders(project, beds or None)
    cfg = project.config
    return json.dumps({
        "producer": project.producer or "",
        "title": project.title or "",
        "beds": project.bed_ids,
        "used": [{
            "extruder": e,
            "label": filament_label(project, e),
            "type": project.filament_type(e) or "",
            "generic_type": normalized_type(project.filament_type(e)),
            "colour": project.extruder_colour(e) or "",
        } for e in used],
        "suggested_map": {str(k): v for k, v in suggest_filament_map(used).items()},
        "print_settings": str(cfg.get("print_settings_id") or "") if cfg else "",
        "printer_settings": str(cfg.get("printer_settings_id") or "") if cfg else "",
        "has_settings": bool(cfg),
    })


def preset_file_printer(path: str) -> str:
    """The printer preset an uploaded Bambu/Orca .3mf / .json names (to pick its table)."""
    cfg = load_config_file(path)
    return str(cfg.get("printer_settings_id") or cfg.get("name") or "")


def convert(request_json: str) -> str:
    """Run one conversion. The request (all keys optional except input, output, slicer)::

        {"input": "/in/x.3mf", "output": "/out/x_bambu.3mf", "slicer": "bambu" | "orca",
         "tables": ["/presets/bambu/BBL.json"],
         "printer": "preset" | "prusa" | "file", "printer_name": "...", "printer_file": "...",
         "process": "...", "filaments": [{"name": "...", "locked": true}, ...]  (per slot),
         "settings": "presets" | "prusa" | "none", "beds": [1, 2], "split": false,
         "filament_map": {"1": 1, "3": 2}}
    """
    req = json.loads(request_json)
    slicer = req.get("slicer") or SLICER_BAMBU
    if slicer not in (SLICER_BAMBU, SLICER_ORCA):
        raise ValueError(f"unknown slicer {slicer!r}")
    tables = []
    for p in req.get("tables") or []:
        with open(p, encoding="utf-8") as f:
            tables.append(json.load(f))
    mode = req.get("printer") or "preset"
    sel = Selection()
    if mode == "prusa":
        printer = PRINTER_PRUSA
    elif mode == "file":
        printer = req["printer_file"]
    else:
        printer = req.get("printer_name") or ""
        if not printer:
            raise ValueError("choose a printer")
        fils = req.get("filaments") or []
        sel = Selection(
            printer=printer,
            process=req.get("process") or "",
            filaments=[str(f.get("name") or "") for f in fils],
            filament_locked=[bool(f.get("locked")) for f in fils],
        )
    install = TableInstall(slicer, tables, sel)
    fmap = req.get("filament_map")
    settings = req.get("settings") or "presets"
    result = convert_file(
        req["input"], req["output"],
        beds=req.get("beds") or None,
        filament_map={int(k): int(v) for k, v in fmap.items()} if fmap is not None else None,
        settings=settings != "none",
        prusa_settings=settings == "prusa",
        printer=printer,
        bed_layout=BEDS_SPLIT if req.get("split") else BEDS_PLATES,
        install=install,
    )
    s = result.stats
    return json.dumps({
        "outputs": result.outputs,
        "slicer": result.slicer_name,
        "description": result.target_description,
        "objects": s.objects_written,
        "parts": s.parts_written,
        "build_items": s.build_items_written,
        "painted_triangles": s.painted_triangles,
        "plates": s.plates_written,
        "filament_map": {str(k): v for k, v in (s.filament_map or {}).items()},
        "settings_written": s.settings_written,
        "printer": s.printer_name,
        "process": s.print_name,
        "filaments": s.filament_names,
        "colour_changes": s.custom_gcodes_written,
        "layer_ranges": s.layer_ranges_written,
        "text_parts": s.text_parts,
        "warnings": s.warnings,
        "names": [os.path.basename(p) for p in result.outputs],
    })
