"""Read-only view of an installed Bambu Studio or OrcaSlicer.

Both slicers keep a data directory with an app config (``BambuStudio.conf`` /
``OrcaSlicer.conf``, JSON, on Windows followed by an MD5 checksum line), system presets
under ``system/<Vendor>.json`` + ``system/<Vendor>/{machine,process,filament}/*.json``
and user presets under ``user/<preset_folder>/{machine,process,filament}/*.json``.

Presets are JSON: system presets inherit from each other by name (``inherits``) and may
``include`` abstract templates; user presets store only their differences from a system
parent, with ``"nil"`` entries in vectors meaning "the parent's value".

Which presets are selected lives in the app config: Bambu Studio keeps
``presets.machine``, ``presets.process`` and ``presets.filaments``; OrcaSlicer keeps only
``presets.machine`` there and the process and filaments of each printer in
``orca_presets[]``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

SLICER_BAMBU = "bambu"
SLICER_ORCA = "orca"

APP_NAMES = {SLICER_BAMBU: "BambuStudio", SLICER_ORCA: "OrcaSlicer"}
DISPLAY_NAMES = {SLICER_BAMBU: "Bambu Studio", SLICER_ORCA: "OrcaSlicer"}
ENV_VARS = {SLICER_BAMBU: "BAMBU_STUDIO_CONFIG_DIR", SLICER_ORCA: "ORCA_SLICER_CONFIG_DIR"}

KINDS = ("machine", "process", "filament")
# Keys of a preset JSON that are bookkeeping, not settings.
META_KEYS = frozenset({
    "name", "from", "version", "inherits", "type", "instantiation", "setting_id", "include",
    "base_id", "updated_time", "user_id", "is_custom_defined", "description",
})

_SUFFIX_3MF = re.compile(r"\s*\((?:[^()]*\.3mf)\)\s*$", re.IGNORECASE)


def parse_conf_text(text: str) -> dict:
    """The leading JSON object of an app config (ignores a trailing ``# MD5 checksum`` line)."""
    text = text.lstrip("﻿")
    try:
        obj, _end = json.JSONDecoder().raw_decode(text.lstrip())
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def strip_project_suffix(name: str) -> str:
    """'Bambu PLA Basic @BBL X1C(foo.3mf)' -> 'Bambu PLA Basic @BBL X1C'."""
    return _SUFFIX_3MF.sub("", name or "").strip()


@dataclass
class Preset:
    kind: str  # machine | process | filament
    name: str
    is_system: bool
    data: dict  # the preset's own JSON
    path: str = ""
    vendor: str = ""

    @property
    def inherits(self) -> str:
        v = self.data.get("inherits", "")
        return v if isinstance(v, str) else ""


@dataclass
class Selection:
    printer: str = ""
    process: str = ""
    filaments: List[str] = field(default_factory=list)
    filament_colours: List[str] = field(default_factory=list)
    bed_type: str = ""  # int enum as stored in the app config, may be ""


class BambuInstall:
    """One installed Bambu Studio or OrcaSlicer configuration directory."""

    def __init__(self, slicer: str, data_dir: str):
        self.slicer = slicer
        self.data_dir = data_dir
        conf_path = os.path.join(data_dir, APP_NAMES[slicer] + ".conf")
        try:
            with open(conf_path, encoding="utf-8", errors="replace") as f:
                self.conf = parse_conf_text(f.read())
        except OSError:
            self.conf = {}
        app = self.conf.get("app") if isinstance(self.conf.get("app"), dict) else {}
        self.app: dict = app
        folder = app.get("preset_folder") or ""
        self.preset_folder = folder if folder else "default"
        self._system_index: Optional[Dict[tuple, Preset]] = None
        self._resolved: Dict[tuple, Optional[dict]] = {}

    # -- discovery ------------------------------------------------------------------
    @staticmethod
    def candidate_dirs(slicer: str) -> List[str]:
        app = APP_NAMES[slicer]
        out: List[str] = []
        env = os.environ.get(ENV_VARS[slicer])
        if env:
            out.append(env)
        home = os.path.expanduser("~")
        bases = []
        if os.environ.get("APPDATA"):
            bases.append(os.environ["APPDATA"])
        bases.append(os.path.join(home, "Library", "Application Support"))
        bases.append(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"))
        names = [app]
        if slicer == SLICER_BAMBU:
            names += [app + "Beta", app + "Internal"]
        for base in bases:
            for n in names:
                out.append(os.path.join(base, n))
        if slicer == SLICER_BAMBU:
            out.append(os.path.join(home, ".var", "app", "com.bambulab.BambuStudio", "config", app))
        else:
            out.append(os.path.join(home, ".var", "app", "com.orcaslicer.OrcaSlicer", "config", app))
            out.append(os.path.join(home, ".var", "app", "io.github.softfever.OrcaSlicer", "config", app))
        return out

    @classmethod
    def find(cls, slicer: str, data_dir: Optional[str] = None) -> Optional["BambuInstall"]:
        app = APP_NAMES[slicer]
        for d in [data_dir] if data_dir else cls.candidate_dirs(slicer):
            if d and os.path.isfile(os.path.join(d, app + ".conf")):
                return cls(slicer, d)
            # a portable Orca keeps its data in <exe dir>/data_dir
            if d and os.path.isfile(os.path.join(d, "data_dir", app + ".conf")):
                return cls(slicer, os.path.join(d, "data_dir"))
        return None

    @property
    def version(self) -> str:
        """The app version that last wrote the config, e.g. '02.03.00.70' or '2.3.1'."""
        v = self.app.get("version")
        if isinstance(v, str) and v.strip():
            return v.strip()
        header = self.conf.get("header")
        if isinstance(header, str):
            m = re.search(r"(\d+\.\d+\.\d+(?:\.\d+)?)", header)
            if m:
                return m.group(1)
        return ""

    @property
    def display_name(self) -> str:
        return DISPLAY_NAMES[self.slicer]

    # -- selections -----------------------------------------------------------------
    def selection(self) -> Selection:
        presets = self.conf.get("presets") if isinstance(self.conf.get("presets"), dict) else {}
        sel = Selection(printer=str(presets.get("machine") or ""))
        entry: dict = {}
        if self.slicer == SLICER_ORCA:
            for e in self.conf.get("orca_presets") or []:
                if isinstance(e, dict) and e.get("machine") == sel.printer:
                    entry = e
                    break
        if entry:
            sel.process = str(entry.get("process") or "")
            fils = [str(entry.get("filament") or "")]
            i = 1
            while f"filament_{i:02d}" in entry:
                fils.append(str(entry[f"filament_{i:02d}"]))
                i += 1
            sel.filaments = [f for f in fils if f]
            colours = entry.get("filament_colors")
            sel.bed_type = str(entry.get("curr_bed_type") or "")
        else:
            sel.process = str(presets.get("process") or "")
            fils = presets.get("filaments")
            if isinstance(fils, list):
                sel.filaments = [str(f) for f in fils if f]
            else:
                fils = [str(presets.get("filament") or "")]
                i = 1
                while f"filament_{i:02d}" in presets:
                    fils.append(str(presets[f"filament_{i:02d}"]))
                    i += 1
                sel.filaments = [f for f in fils if f]
            colours = presets.get("filament_colors")
            sel.bed_type = str(self.app.get("curr_bed_type") or "")
        if isinstance(colours, str):
            sel.filament_colours = [c.strip() for c in colours.split(",") if c.strip()]
        elif isinstance(colours, list):
            sel.filament_colours = [str(c) for c in colours]
        return sel

    def selected_printer(self) -> Optional[str]:
        return self.selection().printer or None

    # -- presets --------------------------------------------------------------------
    def _user_dirs(self, kind: str) -> List[str]:
        base = os.path.join(self.data_dir, "user", self.preset_folder)
        dirs = [os.path.join(base, kind), os.path.join(base, kind, "base")]
        if self.slicer == SLICER_ORCA:
            for sub in ("_local", "_subscribed"):
                root = os.path.join(base, sub)
                if os.path.isdir(root):
                    for bundle in sorted(os.listdir(root)):
                        dirs.append(os.path.join(root, bundle, kind))
        return dirs

    def user_preset(self, kind: str, name: str) -> Optional[Preset]:
        for d in self._user_dirs(kind):
            path = os.path.join(d, name + ".json")
            if os.path.isfile(path):
                data = _read_json(path)
                if data is not None:
                    return Preset(kind, name, False, data, path)
        return None

    def user_preset_names(self, kind: str) -> List[str]:
        out: List[str] = []
        for d in self._user_dirs(kind):
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                if fn.lower().endswith(".json"):
                    data = _read_json(os.path.join(d, fn))
                    if data is None or str(data.get("instantiation", "true")).lower() == "false":
                        continue
                    name = os.path.splitext(fn)[0]
                    if name not in out:
                        out.append(name)
        return out

    def _load_system_index(self) -> Dict[tuple, Preset]:
        """(kind, name) -> Preset for every system preset with a JSON file."""
        if self._system_index is not None:
            return self._system_index
        index: Dict[tuple, Preset] = {}
        sys_dir = os.path.join(self.data_dir, "system")
        if os.path.isdir(sys_dir):
            for fn in sorted(os.listdir(sys_dir)):
                if not fn.lower().endswith(".json"):
                    continue
                vendor = os.path.splitext(fn)[0]
                vend = _read_json(os.path.join(sys_dir, fn))
                if not vend:
                    continue
                for list_key, kind in (("machine_model_list", "machine_model"), ("machine_list", "machine"),
                                       ("process_list", "process"), ("filament_list", "filament")):
                    for entry in vend.get(list_key) or []:
                        if not isinstance(entry, dict):
                            continue
                        name, sub = entry.get("name"), entry.get("sub_path")
                        if not name or not sub:
                            continue
                        path = os.path.join(sys_dir, vendor, sub)
                        data = _read_json(path)
                        if data is None:
                            continue
                        # first definition wins (vendor files are read in name order)
                        index.setdefault((kind, name), Preset(kind, name, True, data, path, vendor))
        self._system_index = index
        return index

    def system_preset(self, kind: str, name: str) -> Optional[Preset]:
        return self._load_system_index().get((kind, name))

    def system_names(self, kind: str) -> List[str]:
        """Names of the system presets of ``kind`` (process / filament / machine)."""
        return sorted(name for (k, name) in self._load_system_index() if k == kind)

    def has_system_profiles(self) -> bool:
        return bool(self._load_system_index())

    def find_preset(self, kind: str, name: str) -> Optional[Preset]:
        """User preset of that name if one exists, else the system preset."""
        if not name:
            return None
        return self.user_preset(kind, name) or self.system_preset(kind, name)

    def resolve(self, kind: str, name: str, _seen: tuple = ()) -> Optional[dict]:
        """A preset's full settings (inherits chain + includes + own keys; nil resolved).

        None when the preset or one of its ancestors cannot be read (for example an
        OrcaSlicer build that ships its system profiles only as binary caches)."""
        key = (kind, name)
        if key in self._resolved:
            r = self._resolved[key]
            return dict(r) if r is not None else None
        preset = self.find_preset(kind, name)
        if preset is None or name in _seen:
            return None
        out: dict = {}
        parent = preset.inherits
        if parent:
            base = self.resolve(kind, parent, _seen + (name,))
            if base is None:
                self._resolved[key] = None
                return None
            out.update(base)
        includes = preset.data.get("include") or []
        if isinstance(includes, str):
            includes = [includes]
        for inc in includes:
            inc_cfg = self.resolve(kind, inc, _seen + (name,))
            if inc_cfg:
                out.update({k: v for k, v in inc_cfg.items() if k not in META_KEYS})
        for k, v in preset.data.items():
            if k in META_KEYS:
                continue
            if isinstance(v, list) and k in out and isinstance(out[k], list):
                parent_v = out[k]
                v = [parent_v[i] if (x == "nil" and i < len(parent_v)) else x for i, x in enumerate(v)]
            out[k] = v
        self._resolved[key] = dict(out)
        return out

    # -- printers -------------------------------------------------------------------
    def _installed_models(self) -> Dict[tuple, set]:
        out: Dict[tuple, set] = {}
        for m in self.conf.get("models") or []:
            if isinstance(m, dict) and m.get("model"):
                key = (str(m.get("vendor") or ""), str(m["model"]))
                nozzles = {n.strip() for n in str(m.get("nozzle_diameter") or "").split(";") if n.strip()}
                out.setdefault(key, set()).update(nozzles)
        return out

    def installed_printers(self) -> List[str]:
        names = list(self.user_preset_names("machine"))
        models = self._installed_models()
        for (kind, name), preset in sorted(self._load_system_index().items(), key=lambda kv: kv[0][1]):
            if kind != "machine" or name in names:
                continue
            if str(preset.data.get("instantiation", "true")).lower() == "false":
                continue
            resolved = self.resolve("machine", name) or preset.data
            model = str(resolved.get("printer_model") or "")
            variant = str(resolved.get("printer_variant") or "")
            nozzles = models.get((preset.vendor, model))
            if nozzles is not None and (not nozzles or variant in nozzles):
                names.append(name)
        sel = self.selected_printer()
        if sel and sel not in names:
            names.insert(0, sel)
        return names


def find_install(slicer: str, data_dir: Optional[str] = None) -> Optional[BambuInstall]:
    return BambuInstall.find(slicer, data_dir)
