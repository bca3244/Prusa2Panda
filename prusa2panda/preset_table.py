"""A slicer "install" backed by a bundled preset table instead of the slicer's data folder.

The web version cannot read the Bambu Studio / OrcaSlicer configuration on the user's PC.
It references the slicer's *system* presets by their exact names instead, which the slicer
resolves when it opens the project (see target.py). What the converter itself needs from
those presets (plate size, nozzle variants, filament type / id / diameter / colour, the
printer's default process and filament, which presets fit which printer) comes from a table
generated from the slicer's ``resources/profiles`` by ``tools/gen_preset_table.py``.

A table is one JSON object per vendor::

    {"format": 1, "slicer": "bambu", "version": "02.09.01.70", "vendor": "BBL",
     "machine": {name: {key: value}}, "process": {...}, "filament": {...}}

holding only the keys in MACHINE_KEYS / PROCESS_KEYS / FILAMENT_KEYS, already resolved
through the presets' ``inherits`` chains.
"""

from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional

from .bambu_install import DISPLAY_NAMES, KINDS, Preset, Selection

TABLE_FORMAT = 1

MACHINE_KEYS = (
    "printable_area", "printable_height", "nozzle_diameter", "printer_extruder_variant",
    "extruder_type", "default_nozzle_volume_type", "default_bed_type", "printer_model",
    "printer_variant", "default_print_profile", "default_filament_profile",
)
PROCESS_KEYS = ("print_extruder_variant", "compatible_printers", "layer_height")
FILAMENT_KEYS = (
    "filament_type", "filament_id", "filament_diameter", "default_filament_colour",
    "filament_extruder_variant", "compatible_printers", "filament_vendor",
)
TABLE_KEYS = {"machine": MACHINE_KEYS, "process": PROCESS_KEYS, "filament": FILAMENT_KEYS}


def _as_list(v) -> List[str]:
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def compatible(cfg: dict, printer: str) -> bool:
    """Whether a process / filament preset lists ``printer`` (an empty list fits any)."""
    cp = _as_list(cfg.get("compatible_printers"))
    return not cp or printer in cp


class TableInstall:
    """The subset of BambuInstall that target.py and convert.py use, from preset tables.

    Every preset is a system preset, so the converter writes it by name only (inherits ""
    and no diff keys) and the slicer fills in its values when it opens the project."""

    def __init__(self, slicer: str, tables: Iterable[dict], selection: Optional[Selection] = None,
                 version: str = ""):
        self.slicer = slicer
        self.presets: Dict[str, Dict[str, dict]] = {k: {} for k in KINDS}
        self.table_version = ""
        for t in tables:
            if t.get("format") != TABLE_FORMAT:
                raise ValueError(f"preset table format {t.get('format')!r} is not {TABLE_FORMAT}")
            if t.get("slicer") != slicer:
                raise ValueError(f"preset table is for {t.get('slicer')!r}, not {slicer!r}")
            self.table_version = self.table_version or str(t.get("version") or "")
            for kind in KINDS:
                for name, cfg in (t.get(kind) or {}).items():
                    self.presets[kind].setdefault(name, cfg)
        self._selection = selection or Selection()
        # The project header's application version. Empty uses the converter's default, an
        # old-enough version that every current slicer build opens without a "newer version"
        # notice (the table may come from a newer slicer than the one the user runs).
        self._version = version
        self.conf: dict = {}

    @classmethod
    def from_json(cls, slicer: str, texts: Iterable[str], selection: Optional[Selection] = None,
                  version: str = "") -> "TableInstall":
        return cls(slicer, [json.loads(t) for t in texts], selection, version)

    # -- what BambuInstall offers -------------------------------------------------------
    @property
    def version(self) -> str:
        return self._version

    @property
    def display_name(self) -> str:
        return DISPLAY_NAMES[self.slicer]

    def selection(self) -> Selection:
        return self._selection

    def selected_printer(self) -> Optional[str]:
        return self._selection.printer or None

    def system_preset(self, kind: str, name: str) -> Optional[Preset]:
        cfg = self.presets.get(kind, {}).get(name)
        return Preset(kind, name, True, dict(cfg)) if cfg is not None else None

    def find_preset(self, kind: str, name: str) -> Optional[Preset]:
        return self.system_preset(kind, name) if name else None

    def resolve(self, kind: str, name: str) -> Optional[dict]:
        cfg = self.presets.get(kind, {}).get(name)
        return dict(cfg) if cfg is not None else None

    def system_names(self, kind: str) -> List[str]:
        return sorted(self.presets.get(kind, {}))

    def has_system_profiles(self) -> bool:
        return any(self.presets.values())

    def installed_printers(self) -> List[str]:
        return self.system_names("machine")

    # -- for the web page ---------------------------------------------------------------
    def processes_for(self, printer: str) -> List[str]:
        return sorted(n for n, c in self.presets["process"].items() if compatible(c, printer))

    def filaments_for(self, printer: str) -> List[str]:
        return sorted(n for n, c in self.presets["filament"].items() if compatible(c, printer))
