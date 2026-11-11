"""PrusaSlicer settings -> Bambu Studio / OrcaSlicer settings, per slot.

``keymap`` holds the rule tables (generated-schema checked); this module applies them to
one project and shapes the results the way target.build_project_settings expects: the
process keys as one dict, the filament keys as one scalar dict per filament slot, the
printer keys as one dict, and per-object / per-part / per-range settings with their
extruder numbers moved to filament slots.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from . import keymap as K
from .bambu_install import SLICER_ORCA
from .prusa_config import PrusaConfig
from .target import SlotAnchor

_EXTRUDER_KEYS = {
    "wall_filament", "sparse_infill_filament", "solid_infill_filament", "support_filament",
    "support_interface_filament", "inner_wall_filament_id", "top_surface_filament_id",
    "bottom_surface_filament_id", "wipe_tower_filament", "extruder",
}
_PLACEHOLDER_RE = re.compile(r"\[(\w+)\]|\{(\w+)")


def _flavor(slicer: str) -> str:
    return "orca" if slicer == SLICER_ORCA else "bambu"


def translate_gcode(text: str) -> str:
    """Rename the PrusaSlicer placeholders the target slicers spell differently."""
    def sub(m):
        name = m.group(1) or m.group(2)
        new = K.GCODE_PLACEHOLDERS.get(name, name)
        return f"[{new}]" if m.group(1) else "{" + new
    return _PLACEHOLDER_RE.sub(sub, text)


class Translator:
    def __init__(self, config: PrusaConfig, slicer: str, warnings: List[str]):
        self.cfg: Dict[str, str] = dict(config.items())
        self.flavor = _flavor(slicer)
        self.S = K.load_schema(self.flavor)
        self.warnings = warnings
        self.n_ext = max(1, len(config.vector("nozzle_diameter") or []))
        self._filaments: Optional[dict] = None

    def _warn(self, items: List[str]) -> None:
        for w in items:
            if w not in self.warnings:
                self.warnings.append(w)

    # -- process -------------------------------------------------------------------------------
    def process_settings(self, variants: int = 1, fmap: Optional[Dict[int, int]] = None) -> dict:
        if not self.cfg:
            return {}
        w: List[str] = []
        out = K.convert_print(self.cfg, self.flavor, w, variants)
        self._warn(w)
        out = {k: v for k, v in out.items() if k in self.S.PRINT_KEYS}
        for k in list(out):
            if k in _EXTRUDER_KEYS:
                out[k] = self._remap(out[k], fmap)
        return out

    # -- filaments -----------------------------------------------------------------------------
    def filament_settings(self, extruder: int) -> dict:
        """Scalar values of the PrusaSlicer filament on ``extruder`` (1-based)."""
        if not self.cfg:
            return {}
        if self._filaments is None:
            w: List[str] = []
            self._filaments = K.convert_filament(self.cfg, self.flavor, w, n=self.n_ext)
            self._warn(w)
        i = max(0, min(extruder - 1, self.n_ext - 1))
        out = {}
        for k, v in self._filaments.items():
            if k == "filament_settings_id" or k not in self.S.FILAMENT_KEYS:
                continue
            if isinstance(v, list):
                if not v:
                    continue
                out[k] = v[min(i, len(v) - 1)]
            else:
                out[k] = v
        return out

    # -- printer -------------------------------------------------------------------------------
    def printer_config(self, n_slots: int) -> dict:
        """A complete custom printer from the PrusaSlicer printer (keep-the-printer mode)."""
        w: List[str] = []
        out = K.convert_printer(self.cfg, self.flavor, w) if self.cfg else {}
        self._warn(w)
        for k in ("machine_start_gcode", "machine_end_gcode", "before_layer_change_gcode",
                  "layer_change_gcode", "change_filament_gcode", "machine_pause_gcode",
                  "template_custom_gcode", "printing_by_object_gcode"):
            if isinstance(out.get(k), str):
                out[k] = translate_gcode(out[k])
        n_src = max(1, len(out.get("nozzle_diameter") or ["0.4"]))
        semm = str(out.get("single_extruder_multi_material", self.cfg.get("single_extruder_multi_material", "0"))) == "1"
        n = n_src
        if semm:
            n = 1  # an MMU feeds one nozzle: one extruder, the filaments are slots
        elif n_src > 1 and self.flavor != "orca":
            n = 1
            self._warn([f"the PrusaSlicer printer has {n_src} tools; Bambu Studio cannot model a tool changer, "
                        "so the custom printer gets one extruder (OrcaSlicer keeps every tool)"])
        if n != n_src:
            for k, v in list(out.items()):
                if isinstance(v, list) and len(v) == n_src and k not in ("printable_area", "bed_exclude_area"):
                    out[k] = v[:n]
        name = self.cfg.get("printer_settings_id") or "PrusaSlicer printer"
        out.setdefault("printable_area", ["0x0", "250x0", "250x210", "0x210"])
        out.setdefault("printable_height", "220")
        out.setdefault("nozzle_diameter", ["0.4"] * n)
        out["printer_settings_id"] = name
        out["printer_model"] = ""
        out.setdefault("printer_variant", (out.get("nozzle_diameter") or ["0.4"])[0])
        out.setdefault("gcode_flavor", "marlin2" if self.flavor == "orca" else "marlin")
        out["extruder_type"] = ["Direct Drive"] * n
        out["extruder_variant_list"] = ["Direct Drive Standard"] * n
        out["printer_extruder_id"] = [str(i + 1) for i in range(n)]
        out["printer_extruder_variant"] = ["Direct Drive Standard"] * n
        out["default_nozzle_volume_type"] = ["Standard"] * n
        out.setdefault("bed_exclude_area", [])
        return {k: v for k, v in out.items() if k in self.S.OPTIONS}

    # -- objects -------------------------------------------------------------------------------
    def object_settings(self, settings: Dict[str, str], fmap: Dict[int, int], label: str) -> Dict[str, str]:
        """Per-object / per-part / per-range PrusaSlicer settings -> target keys."""
        if not settings:
            return {}
        full = dict(self.cfg)
        full.update(settings)
        pv = K.PrusaView(full)
        out: Dict[str, str] = {}
        w: List[str] = []
        allowed = set(self.S.OBJECT_KEYS) | set(self.S.REGION_KEYS)
        for rule in K.object_rules(self.flavor):
            if rule.prusa not in settings:
                continue
            tkey = rule.target(self.flavor)
            v = K.apply_rule(rule, self.flavor, pv.scalar(rule.prusa), pv, w)
            if v is None:
                continue
            s = K.serialize(self.S, tkey, v)
            out[tkey] = s if isinstance(s, str) else ",".join(s)
        special = K.special_print(K.PrusaView(full), self.flavor, [], 1)
        touched = set(settings)
        trigger = {
            "enable_support": {"support_material", "support_material_auto", "support_material_style"},
            "support_type": {"support_material", "support_material_auto", "support_material_style"},
            "support_style": {"support_material_style"},
            "ironing_type": {"ironing", "ironing_type"},
            "wall_sequence": {"external_perimeters_first"},
        }
        for k, srcs in trigger.items():
            if touched & srcs and k in special and k in allowed:
                v = special[k]
                out[k] = v if isinstance(v, str) else ",".join(v)
        for pk, tk in K.OBJECT_SPECIAL.items():
            if pk in settings and tk in (allowed | {"extruder"}):
                out[tk] = settings[pk]
        for k in list(out):
            if k in _EXTRUDER_KEYS:
                out[k] = self._remap(out[k], fmap)
        dropped = sorted(k for k in settings if k not in _covered(settings, out))
        if dropped:
            w.append(f"{label}: no {self.flavor_name} equivalent for {', '.join(dropped)}")
        self._warn(w)
        return {k: v for k, v in out.items() if k in allowed or k == "extruder"}

    @property
    def flavor_name(self) -> str:
        return "OrcaSlicer" if self.flavor == "orca" else "Bambu Studio"

    # -- multi-material -------------------------------------------------------------------------
    def flush_matrix(self, extruders: List[int]) -> Optional[List[List[float]]]:
        """Purge volumes between the PrusaSlicer extruders feeding each filament slot."""
        if not self.cfg or len(extruders) < 2:
            return None
        flat = K.flush_matrix(self.cfg, self.n_ext)
        n = self.n_ext
        out = []
        for a in extruders:
            row = []
            for b in extruders:
                try:
                    row.append(float(flat[(a - 1) * n + (b - 1)]))
                except (IndexError, ValueError):
                    row.append(float(280))
            out.append(row)
        return out

    @staticmethod
    def _remap(value, fmap: Optional[Dict[int, int]]):
        if not fmap:
            return value
        def one(x: str) -> str:
            try:
                n = int(float(x))
            except ValueError:
                return x
            return x if n <= 0 else str(fmap.get(n, n))
        if isinstance(value, list):
            return [one(x) for x in value]
        return one(str(value))


def _covered(settings: Dict[str, str], out: Dict[str, str]) -> set:
    """Prusa keys of ``settings`` that produced some output."""
    covered = set()
    for rule in K.PRINT_RULES:
        if rule.prusa in settings and (rule.bambu in out or rule.orca in out):
            covered.add(rule.prusa)
    for pk, tk in K.OBJECT_SPECIAL.items():
        if pk in settings and tk in out:
            covered.add(pk)
    if "enable_support" in out or "support_type" in out:
        covered |= {"support_material", "support_material_auto", "support_material_style"}
    if "ironing_type" in out:
        covered |= {"ironing", "ironing_type"}
    if "wall_sequence" in out:
        covered.add("external_perimeters_first")
    return covered


def prusa_process_anchor(config: PrusaConfig) -> SlotAnchor:
    """The process slot when the PrusaSlicer printer is kept: a project preset."""
    name = config.get("print_settings_id") or "PrusaSlicer print"
    return SlotAnchor(name=name)


def prusa_filament_anchor(config: PrusaConfig, extruder: int) -> SlotAnchor:
    names = config.vector("filament_settings_id") or []
    name = names[extruder - 1] if 0 < extruder <= len(names) else (names[0] if names else "PrusaSlicer filament")
    return SlotAnchor(name=name or "PrusaSlicer filament")
