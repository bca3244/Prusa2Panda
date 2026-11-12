"""Decide what the converted project prints with, and build its project_settings.config.

Bambu Studio and OrcaSlicer apply a project's ``Metadata/project_settings.config`` like
this (PresetBundle::load_config_file_config / PresetCollection::load_external_preset):
missing keys take the slicer defaults, then every key *not* listed in
``different_settings_to_system[slot]`` is replaced by the value of a base preset
(``inherits_group[slot]``, or the named preset itself when it is a system preset), and the
result is compared with the installed preset named by ``*_settings_id``. Equal means the
installed preset is selected unmodified.

So a project that should open on the user's own presets needs only the preset names, the
anchors and (for user presets) the user's own keys; the slot order of the per-slot vectors
is ``[process, filament 1..N, printer]``. ``filament_colour`` is a project option, not a
filament preset key, so colouring the filaments never marks a preset as modified.
"""

from __future__ import annotations

import json
import os
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .bambu_install import META_KEYS, SLICER_BAMBU, SLICER_ORCA, BambuInstall, strip_project_suffix
from .keymap import load_schema

PRINTER_AUTO = "auto"
PRINTER_PRUSA = "prusa"

DEFAULT_APPLICATION_VERSION = "02.01.00.00"
DEFAULT_ORCA_VERSION = "2.3.0"
DEFAULT_VARIANT = "Direct Drive Standard"
DEFAULT_FLUSH = 280
DEFAULT_BED_TYPE = "Textured PEI Plate"  # PrusaSlicer has no bed type; its sheets are closest to PEI

# Plate sizes of Bambu Lab printers (printable area), used when the printer preset
# cannot be read (e.g. OrcaSlicer builds that ship binary profile caches only).
_PLATE_SIZES = (
    ("A1 mini", (180.0, 180.0)),
    ("H2D", (350.0, 320.0)),
    ("H2S", (340.0, 320.0)),
    ("H2C", (350.0, 320.0)),
    ("X1E", (256.0, 256.0)),
    ("", (256.0, 256.0)),
)

# app config curr_bed_type (an int) -> plate type name (BedType enums differ between the apps).
_BED_TYPES = {
    SLICER_BAMBU: {"1": "Cool Plate", "2": "Engineering Plate", "3": "High Temp Plate",
                   "4": "Textured PEI Plate", "5": "Supertack Plate"},
    SLICER_ORCA: {"1": "Cool Plate", "2": "Engineering Plate", "3": "High Temp Plate",
                  "4": "Textured PEI Plate", "5": "Textured Cool Plate", "6": "Supertack Plate"},
}

_GENERIC_TYPE = {
    "PLA": "PLA", "PETG": "PETG", "PCTG": "PETG", "PET": "PETG", "ABS": "ABS", "ASA": "ASA",
    "FLEX": "TPU", "TPU": "TPU", "PC": "PC", "PA": "PA", "NYLON": "PA", "PVA": "PVA",
    "HIPS": "HIPS", "PP": "PP", "PVB": "PVB",
}


def normalized_type(t: Optional[str]) -> str:
    t = (t or "").strip().upper()
    return _GENERIC_TYPE.get(t, t.split("-")[0] if t else "")


def _first(v) -> str:
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return "" if v is None else str(v)


def _as_list(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


@dataclass
class SlotAnchor:
    """How one slot (process, a filament, the printer) is written."""

    name: str  # *_settings_id
    inherits: str = ""  # inherits_group entry
    diff: List[str] = field(default_factory=list)  # different_settings_to_system keys
    values: Dict[str, object] = field(default_factory=dict)  # values for the diff keys
    resolved: Optional[dict] = None  # full resolved preset, when readable
    note: str = ""


@dataclass
class Target:
    slicer: str = SLICER_BAMBU
    install: Optional[BambuInstall] = None
    printer: Optional[SlotAnchor] = None
    process: Optional[SlotAnchor] = None
    filaments: List[SlotAnchor] = field(default_factory=list)  # per filament slot
    custom_printer: Optional[dict] = None  # a full printer config (keep-the-Prusa-printer mode)
    bed_type: str = ""  # plate type selected in the slicer, by name
    description: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return "OrcaSlicer" if self.slicer == SLICER_ORCA else "Bambu Studio"

    @property
    def printer_config(self) -> dict:
        if self.custom_printer is not None:
            return self.custom_printer
        if self.printer is not None and self.printer.resolved:
            return self.printer.resolved
        return {}

    @property
    def plate(self) -> tuple:
        """((width, depth), (min_x, min_y)) of the target printer's printable area."""
        xs, ys = [], []
        for p in _as_list(self.printer_config.get("printable_area")):
            try:
                x, y = p.split("x")
                xs.append(float(x))
                ys.append(float(y))
            except ValueError:
                pass
        if len(xs) >= 3:
            return (max(xs) - min(xs), max(ys) - min(ys)), (min(xs), min(ys))
        name = self.printer.name if self.printer else ""
        for key, size in _PLATE_SIZES:
            if key in name:
                return size, (0.0, 0.0)
        return (256.0, 256.0), (0.0, 0.0)

    @property
    def extruder_count(self) -> int:
        return max(1, len(_as_list(self.printer_config.get("nozzle_diameter"))))

    def application(self) -> tuple:
        """(Application metadata, OrcaSlicer metadata) for the 3mf header.

        The version must not be newer than the installed slicer, or it opens the file as
        geometry only / with a "newer version" dialog; 2.0 or later avoids the old-file
        migrations."""
        ver = self.install.version if self.install else ""
        if self.slicer == SLICER_ORCA:
            orca = ver if ver and ver[0].isdigit() and not ver.startswith("0") else DEFAULT_ORCA_VERSION
            return f"BambuStudio-{DEFAULT_APPLICATION_VERSION}", orca
        if ver.count(".") == 3 and ver.startswith("0") and ver >= "02.00.00.00":
            return f"BambuStudio-{ver}", ""
        return f"BambuStudio-{DEFAULT_APPLICATION_VERSION}", ""


# -- anchors --------------------------------------------------------------------------------

def make_anchor(install: Optional[BambuInstall], kind: str, name: str, warnings: list) -> SlotAnchor:
    """The cleanest way to reference an installed preset (see module docstring)."""
    anchor = SlotAnchor(name=name)
    if install is None or not name:
        return anchor
    preset = install.find_preset(kind, name)
    if preset is None:
        anchor.note = "not installed"
        return anchor
    anchor.resolved = install.resolve(kind, name)
    if preset.is_system:
        return anchor  # inherits "" + empty diff: the installed preset itself
    parent = preset.inherits
    parent_preset = install.system_preset(kind, parent) if parent else None
    if parent_preset is not None and anchor.resolved is not None:
        keys = [k for k in preset.data if k not in META_KEYS and not k.endswith("_settings_id")]
        anchor.inherits = parent
        anchor.diff = keys
        anchor.values = {k: anchor.resolved[k] for k in keys if k in anchor.resolved}
        return anchor
    # A user preset without a readable system parent: anchor it on itself.
    anchor.inherits = name
    if kind != "process":
        anchor.note = "anchored on itself"
    return anchor


def _resolve_selected(install: BambuInstall, kind: str, name: str) -> str:
    """A selected name, repaired when it is a project preset left by an opened 3mf."""
    if not name:
        return ""
    if install.find_preset(kind, name) is not None:
        return name
    stripped = strip_project_suffix(name)
    if stripped != name and install.find_preset(kind, stripped) is not None:
        return stripped
    return name if not install.has_system_profiles() else ""


def _compatible(preset_cfg: Optional[dict], printer_name: str) -> bool:
    if not preset_cfg:
        return True
    cp = _as_list(preset_cfg.get("compatible_printers"))
    return not cp or printer_name in cp


def pick_filament(install: BambuInstall, current: str, printer: str, prusa_type: str,
                  default: str) -> str:
    """The user's filament for a slot, unless its material differs from the PrusaSlicer
    filament on that extruder: then a Generic preset of that material for the printer."""
    want = normalized_type(prusa_type)
    name = current or default
    cfg = install.resolve("filament", name) if name else None
    if cfg is not None and want:
        have = normalized_type(_first(cfg.get("filament_type")))
        if have and have != want:
            found = _generic_filament(install, printer, want)
            if found:
                return found
    return name


def _generic_filament(install: BambuInstall, printer: str, want: str) -> Optional[str]:
    """A selectable system filament of material ``want`` for ``printer``: Generic first,
    then the slicer vendor's Basic line, then any other of that material."""
    best = None
    for cand in install.system_names("filament"):
        if not (cand.startswith("Generic ") or cand.startswith("Bambu ")) or want not in cand.upper():
            continue
        cfg = install.resolve("filament", cand)
        if not cfg or str(cfg.get("instantiation", "true")).lower() == "false":
            continue
        if normalized_type(_first(cfg.get("filament_type"))) != want:
            continue
        if not _as_list(cfg.get("compatible_printers")) or not _compatible(cfg, printer):
            continue
        rank = (0 if cand.startswith(f"Generic {want} ") or cand == f"Generic {want}" else
                1 if cand.startswith(f"Bambu {want} Basic") else 2, len(cand), cand)
        if best is None or rank < best[0]:
            best = (rank, cand)
    if best:
        return best[1]
    for cand in (f"Generic {want} @System", f"Generic {want}"):  # OrcaSlicer's generic presets
        cfg = install.resolve("filament", cand)
        if cfg and str(cfg.get("instantiation", "true")).lower() != "false" and _compatible(cfg, printer):
            return cand
    return None


# -- resolving the target ---------------------------------------------------------------------

def resolve_target(
    slicer: str,
    install: Optional[BambuInstall],
    printer: str,
    n_filaments: int,
    prusa_types: List[str],
) -> Target:
    """Which printer / process / filament presets the output uses.

    ``printer`` is "auto" (the printer selected in the slicer), "prusa" (keep the
    PrusaSlicer printer as a custom printer), an installed preset name, or a path to a
    Bambu/Orca .3mf / project_settings.config / exported .json preset.
    ``prusa_types`` is the PrusaSlicer filament type of each target filament slot.
    """
    tgt = Target(slicer=slicer, install=install)
    spec = (printer or PRINTER_AUTO).strip()
    w = tgt.warnings
    if spec.lower() == PRINTER_PRUSA:
        tgt.description = "PrusaSlicer printer kept as a custom printer"
        return tgt

    file_cfg: Optional[dict] = None
    locked: List[bool] = []  # per selected filament: keep it whatever its material
    if spec.lower() == PRINTER_AUTO:
        if install is None:
            tgt.description = f"{tgt.display_name} configuration not found; PrusaSlicer printer kept as a custom printer"
            return tgt
        sel = install.selection()
        printer_name = _resolve_selected(install, "machine", sel.printer)
        if not printer_name:
            tgt.description = f"no printer selected in {tgt.display_name}; PrusaSlicer printer kept as a custom printer"
            return tgt
        process_name = _resolve_selected(install, "process", sel.process)
        filaments = [_resolve_selected(install, "filament", f) for f in sel.filaments]
        locked = list(sel.filament_locked)
        tgt.bed_type = _BED_TYPES[slicer].get(sel.bed_type, "")
        source = f"{tgt.display_name} {install.version} (currently selected printer)".replace("  ", " ")
    elif os.path.isfile(spec):
        file_cfg = load_config_file(spec)
        printer_name = str(file_cfg.get("printer_settings_id") or file_cfg.get("name") or "")
        process_name = str(file_cfg.get("print_settings_id") or "")
        filaments = _as_list(file_cfg.get("filament_settings_id"))
        source = f"file {os.path.basename(spec)}"
        if not printer_name:
            raise ValueError(f"{spec!r} does not name a printer preset (no printer_settings_id)")
    else:
        if install is None:
            raise ValueError(
                f"{tgt.display_name} configuration not found, so printer preset {spec!r} cannot be resolved. "
                "Use --printer prusa, or point --printer at an exported .json / .3mf."
            )
        if install.find_preset("machine", spec) is None:
            names = install.installed_printers()
            hint = ", ".join(names[:12]) + (" ..." if len(names) > 12 else "")
            raise ValueError(f"{tgt.display_name} printer preset {spec!r} not found. Installed: {hint}")
        printer_name = spec
        sel = install.selection()
        if sel.printer == spec:
            process_name = _resolve_selected(install, "process", sel.process)
            filaments = [_resolve_selected(install, "filament", f) for f in sel.filaments]
            locked = list(sel.filament_locked)
        else:
            process_name, filaments = "", []
            if slicer == SLICER_ORCA:
                for e in install.conf.get("orca_presets") or []:
                    if isinstance(e, dict) and e.get("machine") == spec:
                        process_name = str(e.get("process") or "")
                        filaments = [str(e.get("filament") or "")]
                        break
        source = f"{tgt.display_name} preset"

    tgt.printer = make_anchor(install, "machine", printer_name, w)
    pcfg = tgt.printer.resolved or {}
    if file_cfg is not None and tgt.printer.resolved is None:
        # Not installed here: carry the file's printer as a project preset, printer keys only
        # (a project file also holds process and per-filament keys sized for its own filaments).
        S = load_schema("orca" if slicer == SLICER_ORCA else "bambu")
        keep = set(S.PRINTER_KEYS) | set(S.MACHINE_LIMITS_KEYS) | set(S.EXTRUDER_KEYS) | {"printer_settings_id"}
        tgt.custom_printer = {k: v for k, v in file_cfg.items() if k in keep}
    if not process_name:
        process_name = str(pcfg.get("default_print_profile") or "")
    tgt.process = make_anchor(install, "process", process_name, w)
    default_fil = _first(pcfg.get("default_filament_profile"))
    if len(locked) != len(filaments) or not all(filaments):
        locked = []
    filaments = [f for f in filaments if f] or ([default_fil] if default_fil else [])
    for i in range(max(1, n_filaments)):
        cur = filaments[i] if i < len(filaments) else (filaments[-1] if filaments else "")
        ptype = prusa_types[i] if i < len(prusa_types) else ""
        if i < len(locked) and locked[i]:
            name = cur
        else:
            name = pick_filament(install, cur, printer_name, ptype, default_fil) if install else cur
        tgt.filaments.append(make_anchor(install, "filament", name, w))
    for a in [tgt.printer, tgt.process, *tgt.filaments]:
        if a.note == "not installed" and install is not None:
            w.append(f"preset {a.name!r} is not installed in {tgt.display_name}; it opens as a project preset")
        elif a.note == "anchored on itself":
            w.append(f"user preset {a.name!r} has no readable system parent; "
                     f"{tgt.display_name} may show a 'Customized Preset' notice when opening")
    used = sorted({a.name for a in tgt.filaments if a.name})
    tgt.description = (
        f"printer '{printer_name}' from {source}; process '{tgt.process.name}'; filaments "
        + ", ".join(f"'{n}'" for n in used)
    )
    return tgt


def load_config_file(path: str) -> dict:
    """A Bambu/Orca .3mf project's settings, a project_settings.config, or a .json preset."""
    if path.lower().endswith(".3mf"):
        with zipfile.ZipFile(path) as z:
            try:
                data = z.read("Metadata/project_settings.config")
            except KeyError:
                raise ValueError(f"{path!r} has no Metadata/project_settings.config; not a Bambu/Orca project")
        cfg = json.loads(data.decode("utf-8", "replace"))
    else:
        with open(path, encoding="utf-8", errors="replace") as f:
            cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"{path!r} is not a Bambu/Orca config")
    if "printer_settings_id" not in cfg and cfg.get("type") == "machine" and cfg.get("name"):
        cfg["printer_settings_id"] = cfg["name"]
    return cfg


# -- project_settings.config ------------------------------------------------------------------

def _active_variant(pcfg: dict) -> tuple:
    """(filament extruder variant, nozzle volume type) the printer prints with by default."""
    ext = _first(pcfg.get("extruder_type")) or "Direct Drive"
    vol = _first(pcfg.get("default_nozzle_volume_type")) or "Standard"
    variants = _as_list(pcfg.get("printer_extruder_variant"))
    want = f"{ext} {vol}"
    if variants and want not in variants:
        want = variants[0]
    return want, vol


def _pick_variant_value(cfg: dict, key: str, variant: str):
    vals = cfg.get(key)
    if not isinstance(vals, list):
        return vals
    variants = _as_list(cfg.get("filament_extruder_variant"))
    if len(variants) == len(vals) and variant in variants:
        return vals[variants.index(variant)]
    return vals[0] if vals else ""


def build_project_settings(
    tgt: Target,
    colours: List[str],
    extra_process: Optional[dict] = None,
    extra_filament: Optional[List[dict]] = None,
    extra_printer: Optional[dict] = None,
    flush_matrix: Optional[List[List[float]]] = None,
) -> dict:
    """The JSON for Metadata/project_settings.config.

    ``colours`` gives one "#RRGGBB" per filament slot (this defines the filament count).
    ``extra_*`` are translated PrusaSlicer settings to write on top of the presets: they
    become diff keys of their slot, so the slicer shows the preset as modified.
    """
    n = max(1, len(colours))
    anchors = list(tgt.filaments)
    while len(anchors) < n:
        anchors.append(anchors[-1] if anchors else SlotAnchor(name=""))
    anchors = anchors[:n]
    pcfg = tgt.printer_config
    variant, volume_type = _active_variant(pcfg)
    app_ver = tgt.application()[0].split("-", 1)[1]

    cfg: dict = {
        "name": "project_settings",
        "from": "project",
        "version": app_ver,
        "printer_technology": "FFF",
    }
    process = tgt.process or SlotAnchor(name="")
    printer = tgt.printer or SlotAnchor(name="")
    if tgt.custom_printer is not None:
        for k, v in tgt.custom_printer.items():
            if k not in META_KEYS or k in ("inherits",):
                cfg[k] = v
    cfg["print_settings_id"] = process.name
    cfg["printer_settings_id"] = printer.name or str(pcfg.get("printer_settings_id") or "")
    cfg["filament_settings_id"] = [a.name for a in anchors]

    diffs: List[List[str]] = [list(process.diff)] + [list(a.diff) for a in anchors] + [list(printer.diff)]
    inherits = [process.inherits] + [a.inherits for a in anchors] + [printer.inherits]

    # User preset keys (exact anchoring).
    for k, v in process.values.items():
        cfg[k] = v
    for k, v in printer.values.items():
        cfg[k] = v
    fil_keys = sorted({k for a in anchors for k in a.values})
    for k in fil_keys:
        vals: List[Optional[str]] = []
        for a in anchors:
            src = a.values if k in a.values else (a.resolved or {})
            vals.append(str(_pick_variant_value(src, k, variant)) if k in src else None)
        if all(v is not None for v in vals):  # an empty value is a value, not an unknown one
            cfg[k] = vals
        else:  # some filament's value is unknown: fall back to self-anchoring those filaments
            for i, a in enumerate(anchors):
                if k in a.values:
                    inherits[1 + i] = a.name
                    diffs[1 + i] = []

    # Translated PrusaSlicer settings.
    if extra_process:
        cfg.update(extra_process)
        diffs[0] = sorted(set(diffs[0]) | set(extra_process))
    if extra_printer:
        cfg.update(extra_printer)
        diffs[-1] = sorted(set(diffs[-1]) | set(extra_printer))
    if extra_filament:
        keys = sorted({k for d in extra_filament for k in d})
        for k in keys:
            vals = []
            for i, a in enumerate(anchors):
                d = extra_filament[i] if i < len(extra_filament) else {}
                if k in d:
                    vals.append(str(d[k]))
                else:
                    src = a.resolved or {}
                    vals.append(str(_pick_variant_value(src, k, variant)) if k in src else "")
            if any(v == "" for v in vals):
                fill = next((v for v in vals if v), "")
                vals = [v or fill for v in vals]
            cfg[k] = vals
            for i in range(n):
                d = extra_filament[i] if i < len(extra_filament) else {}
                if k in d:
                    diffs[1 + i] = sorted(set(diffs[1 + i]) | {k})

    cfg["inherits_group"] = inherits
    cfg["different_settings_to_system"] = [";".join(d) for d in diffs]

    # Project-level filament bookkeeping.
    cfg["filament_colour"] = colours[:n]
    cfg["filament_multi_colour"] = colours[:n]
    cfg["filament_colour_type"] = ["1"] * n
    diam = []
    ids = []
    for a in anchors:
        r = a.resolved or {}
        diam.append(_first(r.get("filament_diameter")) or "1.75")
        ids.append(_first(r.get("filament_id")) or "")
    cfg.setdefault("filament_diameter", diam)
    if all(ids):
        cfg["filament_ids"] = ids
    cfg["filament_extruder_variant"] = [variant] * n
    cfg["filament_self_index"] = [str(i) for i in range(1, n + 1)]
    n_ext = max(1, len(_as_list(pcfg.get("nozzle_diameter"))))
    cfg["filament_map"] = ["1"] * n
    cfg["filament_map_mode"] = "Auto For Flush"
    cfg["filament_volume_map"] = ["0"] * n
    cfg["filament_nozzle_map"] = ["0"] * n
    cfg["nozzle_volume_type"] = [volume_type] * n_ext
    matrix = []
    for _e in range(n_ext):
        for i in range(n):
            for j in range(n):
                if i == j:
                    matrix.append("0")
                elif flush_matrix and i < len(flush_matrix) and j < len(flush_matrix[i]):
                    matrix.append(f"{flush_matrix[i][j]:g}")
                else:
                    matrix.append(str(DEFAULT_FLUSH))
    cfg["flush_volumes_matrix"] = matrix
    cfg["flush_volumes_vector"] = ["140"] * (2 * n)
    cfg["flush_multiplier"] = ["1"] * n_ext
    bed = _first(pcfg.get("default_bed_type"))
    if "curr_bed_type" not in cfg:
        cfg["curr_bed_type"] = tgt.bed_type or bed or DEFAULT_BED_TYPE
    for k in ("printer_model", "printer_variant", "nozzle_diameter", "extruder_type", "printable_area",
              "printable_height"):
        if k in pcfg and k not in cfg:
            cfg[k] = pcfg[k]
    return cfg
