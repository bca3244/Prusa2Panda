"""High-level conversion orchestration (the API the CLI and GUI use)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import ns
from .bambu_install import DISPLAY_NAMES, SLICER_BAMBU, SLICER_ORCA, BambuInstall
from .paint import PaintError, used_states
from .reader import PAINT_COLOR, PrusaProject, read_prusa_3mf
from .target import PRINTER_AUTO, PRINTER_PRUSA, Target, build_project_settings, resolve_target
from .writer import WritePlan, WriteStats, write_bambu_3mf
from . import translate

SLICER_AUTO = "auto"
SLICERS = (SLICER_AUTO, SLICER_BAMBU, SLICER_ORCA)
BEDS_PLATES = "plates"  # one file, PrusaSlicer bed k on plate k
BEDS_SPLIT = "split"  # one output file per bed

__all__ = [
    "BEDS_PLATES", "BEDS_SPLIT", "PRINTER_AUTO", "PRINTER_PRUSA", "SLICER_AUTO", "SLICERS",
    "SLICER_BAMBU", "SLICER_ORCA", "ConversionResult", "convert_file", "current_printer", "describe",
    "detect_slicers", "filament_label", "installed_printers", "slicer_display_name",
    "suggest_filament_map", "used_extruders",
]


@dataclass
class ConversionResult:
    input_path: str
    output_path: str
    stats: WriteStats
    producer: str
    beds_available: list
    slicer_name: str = ""
    target_description: str = ""
    outputs: List[str] = field(default_factory=list)


# -- inspection ---------------------------------------------------------------------------------

def describe(input_path: str) -> PrusaProject:
    """Read a project for inspection (used by --info and the interactive prompts)."""
    return read_prusa_3mf(input_path)


# PrusaSlicer keys whose value is a 1-based extruder number (0 = default / current).
EXTRUDER_SETTING_KEYS = (
    "extruder", "perimeter_extruder", "infill_extruder", "solid_infill_extruder",
    "support_material_extruder", "support_material_interface_extruder", "wipe_tower_extruder",
)


def _ext_values(settings: Dict[str, str]) -> set:
    out = set()
    for k in EXTRUDER_SETTING_KEYS:
        try:
            n = int(float(str(settings.get(k, "0")).strip() or 0))
        except ValueError:
            continue
        if n > 0:
            out.add(n)
    return out


def used_extruders(project: PrusaProject, beds: Optional[List[int]] = None,
                   include_config: bool = False) -> List[int]:
    """Sorted 1-based PrusaSlicer extruders the (selected part of the) project prints with:
    object and part extruders, every painted state, extruder-valued per-object / per-part /
    per-range settings and tool changes. 0 / unset means extruder 1 for an object and the
    object's extruder for a part. ``include_config`` adds the print settings' own extruder
    keys (support / wipe tower extruder), which only matter when those are translated."""
    wanted = list(beds or project.bed_ids)
    objs = sorted({bi.object_index for bi in project.build_items if bi.bed in wanted})
    used: set = set()
    codes: set = set()
    for oi in objs:
        mo = project.objects[oi]
        obj_ext = mo.extruder or 1
        used |= _ext_values(mo.settings)
        for vol in mo.volumes:
            if vol.volume_type in ("ModelPart", "NegativeVolume", "ParameterModifier"):
                if vol.volume_type == "ModelPart" or vol.extruder:
                    used.add(vol.extruder or obj_ext)
            used |= _ext_values(vol.settings)
        for rng in project.layer_ranges.get(mo.index, []):
            used |= _ext_values(rng.settings)
        for tri in mo.mesh.iter(ns.q(ns.CORE, "triangle")):
            c = tri.get(PAINT_COLOR)
            if c:
                codes.add(c)
    for c in codes:
        try:
            used |= used_states(c)
        except PaintError:
            pass
    if objs:
        for b in wanted:
            bg = project.custom_gcodes.get(b)
            for it in (bg.items if bg else []):
                if it.type == 2 and it.extruder > 0:  # tool change
                    used.add(it.extruder)
    if include_config and objs:
        cfg = dict(project.config.items())
        # Print-level perimeter / infill extruders yield to the object's extruder, so only the
        # support and wipe tower extruders add filaments, and only when those are printed.
        support = cfg.get("support_material") == "1" or any(
            project.objects[oi].settings.get("support_material") == "1" for oi in objs)
        keys = []
        if support:
            keys += ["support_material_extruder", "support_material_interface_extruder"]
        if cfg.get("wipe_tower") == "1":
            keys.append("wipe_tower_extruder")
        used |= _ext_values({k: cfg.get(k, "0") for k in keys})
    used.discard(0)
    if not used and objs:
        used.add(1)
    return sorted(used)


def suggest_filament_map(used: List[int]) -> Dict[int, int]:
    """Compact the used extruders to filament slots 1..K."""
    return {old: i for i, old in enumerate(sorted(used), start=1)}


def filament_label(project: PrusaProject, extruder: int) -> str:
    """'3 (PETG #3EC0FF)'."""
    bits = [b for b in (project.filament_type(extruder), project.extruder_colour(extruder)) if b]
    return f"{extruder} ({' '.join(bits)})" if bits else str(extruder)


# -- installs -----------------------------------------------------------------------------------

def slicer_display_name(slicer: str) -> str:
    return DISPLAY_NAMES.get(slicer, slicer)


def detect_slicers(config_dir: Optional[str] = None) -> List[str]:
    """Installed target slicers, Bambu Studio first."""
    return [s for s in (SLICER_BAMBU, SLICER_ORCA) if BambuInstall.find(s, config_dir) is not None]


def pick_slicer(slicer: str, config_dir: Optional[str] = None) -> str:
    if slicer in (SLICER_BAMBU, SLICER_ORCA):
        return slicer
    found = detect_slicers(config_dir)
    return found[0] if found else SLICER_BAMBU


def installed_printers(slicer: str = SLICER_AUTO, config_dir: Optional[str] = None) -> List[str]:
    inst = BambuInstall.find(pick_slicer(slicer, config_dir), config_dir)
    return inst.installed_printers() if inst else []


def current_printer(slicer: str = SLICER_AUTO, config_dir: Optional[str] = None) -> Optional[str]:
    inst = BambuInstall.find(pick_slicer(slicer, config_dir), config_dir)
    return inst.selected_printer() if inst else None


# -- conversion ---------------------------------------------------------------------------------

def _final_map(used: List[int], filament_map: Optional[Dict[int, int]]) -> Dict[int, int]:
    if filament_map is None:
        return suggest_filament_map(used)
    return {e: int(filament_map.get(e, e)) for e in used}


def convert_file(
    input_path: str,
    output_path: str,
    beds: Optional[list] = None,
    filament_map: Optional[dict] = None,
    settings: bool = True,
    prusa_settings: bool = False,
    printer: str = PRINTER_AUTO,
    use_base_presets: bool = True,
    keep_filament_colours: bool = False,
    config_dir: Optional[str] = None,
    slicer: str = SLICER_AUTO,
    bed_layout: str = BEDS_PLATES,
) -> ConversionResult:
    """Convert a PrusaSlicer .3mf at ``input_path`` to a Bambu Studio / OrcaSlicer .3mf.

    ``beds`` restricts the output to these 1-based PrusaSlicer beds (default: all).
    ``filament_map`` maps PrusaSlicer extruders to filament slots (``{}`` keeps the
    numbers; None compacts the used extruders to 1..K). ``settings=False`` writes geometry,
    filaments and painting only. ``prusa_settings`` translates the PrusaSlicer print and
    filament settings on top of the presets; ``use_base_presets=False`` layers them on the
    slicer defaults instead. ``printer`` picks the printer preset (see target.resolve_target).
    With several beds, ``bed_layout`` puts each on its own plate of one file (default) or
    writes one file per bed (``output_bedN.3mf``).
    """
    if os.path.abspath(output_path) == os.path.abspath(input_path):
        raise ValueError("the output file must not be the input file")
    project = read_prusa_3mf(input_path)
    slicer = pick_slicer(slicer, config_dir)
    install = BambuInstall.find(slicer, config_dir)
    wanted = [b for b in (beds or project.bed_ids) if b in project.bed_ids]
    if beds and not wanted:
        raise ValueError(f"bed(s) {beds} not in this project; it has beds {project.bed_ids}")
    wanted = wanted or project.bed_ids

    translating = settings and (prusa_settings or not use_base_presets or printer.strip().lower() == PRINTER_PRUSA
                                or install is None)
    used = used_extruders(project, wanted, include_config=translating)
    fmap = _final_map(used, filament_map)
    n_slots = max([1] + list(fmap.values()))
    ext_of_slot: Dict[int, int] = {}
    for e in sorted(fmap):
        ext_of_slot.setdefault(fmap[e], e)

    warnings: List[str] = []
    if project.has_full_spectrum:
        warnings.append("PrusaSlicer 'full spectrum' virtual extruders have no Bambu/Orca equivalent; not carried over")
    warnings.extend(project.read_warnings)
    if project.off_bed_items:
        warnings.append(f"{project.off_bed_items} instance(s) sit outside every PrusaSlicer bed (PrusaSlicer "
                        "does not print them); they are placed with bed 1")
    if project.has_cut_info:
        warnings.append("cut-object connector links are not carried over (the cut parts are)")
    if project.extruder_count > 16 or n_slots > 16:
        warnings.append("more than 16 filaments: painting beyond filament 16 may not load correctly")

    tgt: Optional[Target] = None
    project_settings = None
    object_settings = None
    description = "settings not converted"
    plan = WritePlan(slicer=slicer, title=project.title)

    if settings:
        prusa_types = [project.filament_type(ext_of_slot.get(s, s)) for s in range(1, n_slots + 1)]
        tgt = resolve_target(slicer, install, printer, n_slots, prusa_types)
        warnings.extend(tgt.warnings)
        keep_prusa_printer = tgt.printer is None
        tr = translate.Translator(project.config, slicer, warnings)
        if keep_prusa_printer:
            tgt.custom_printer = tr.printer_config(n_slots)
        colours = []
        for s in range(1, n_slots + 1):
            e = ext_of_slot.get(s)
            c = project.extruder_colour(e) if e else ""
            if keep_filament_colours or not c:
                c = _preset_colour(tgt, install, s) or c or "#FFFFFF"
            colours.append(c)
        extra_process = extra_printer = None
        extra_filament = None
        want_translation = prusa_settings or not use_base_presets or keep_prusa_printer
        if want_translation:
            variants = 1
            if tgt.process is not None and tgt.process.resolved:
                variants = max(1, len(tgt.process.resolved.get("print_extruder_variant") or []))
            extra_process = tr.process_settings(variants, fmap)
            extra_filament = [tr.filament_settings(ext_of_slot.get(s, s), keep_prusa_printer)
                              for s in range(1, n_slots + 1)]
            if not use_base_presets and tgt.process is not None:
                tgt.process.inherits = ""
                tgt.process.values = {}
        if keep_prusa_printer:
            tgt.process = translate.prusa_process_anchor(project.config)
            tgt.filaments = [translate.prusa_filament_anchor(project.config, ext_of_slot.get(s, s))
                             for s in range(1, n_slots + 1)]
        project_settings = build_project_settings(
            tgt, colours, extra_process, extra_filament, extra_printer,
            # PrusaSlicer's purge volumes only fit when its printer is kept: a Bambu printer
            # flushes into its chute and needs far more than an MMU wipe tower does.
            flush_matrix=(tr.flush_matrix([ext_of_slot.get(s, s) for s in range(1, n_slots + 1)])
                          if keep_prusa_printer else None),
        )
        object_settings = lambda d, label: tr.object_settings(d, fmap, label)  # noqa: E731
        plan.application, plan.orca_version = tgt.application()
        size, origin = tgt.plate
        plan.plate_size, plan.plate_min = size, origin
        description = tgt.description
        if want_translation:
            description += "; PrusaSlicer print and filament settings translated"
        else:
            description += "; PrusaSlicer colours applied to the filaments"
    else:
        if install is not None:
            tgt = resolve_target(slicer, install, printer, n_slots, [])
            plan.application, plan.orca_version = tgt.application()
            plan.plate_size, plan.plate_min = tgt.plate
    plan.project_settings = project_settings
    plan.object_settings = object_settings

    split = bed_layout == BEDS_SPLIT and len(wanted) > 1
    jobs = [([b], _bed_path(output_path, b)) for b in wanted] if split else [(wanted, output_path)]
    stats: Optional[WriteStats] = None
    outputs: List[str] = []
    for job_beds, path in jobs:
        st = write_bambu_3mf(project, path, plan, beds=job_beds, filament_map=fmap)
        outputs.append(path)
        if stats is None:
            stats = st
        else:
            stats.objects_written += st.objects_written
            stats.parts_written += st.parts_written
            stats.build_items_written += st.build_items_written
            stats.painted_triangles += st.painted_triangles
            stats.custom_gcodes_written += st.custom_gcodes_written
            stats.layer_ranges_written += st.layer_ranges_written
            stats.text_parts += st.text_parts
            for wn in st.warnings:
                if wn not in stats.warnings:
                    stats.warnings.append(wn)
    assert stats is not None
    for wn in warnings:
        if wn not in stats.warnings:
            stats.warnings.insert(0, wn)
    stats.filament_map = {k: v for k, v in fmap.items() if k != v} or None
    return ConversionResult(
        input_path=input_path,
        output_path=output_path,
        stats=stats,
        producer=project.producer,
        beds_available=project.bed_ids,
        slicer_name=slicer_display_name(slicer),
        target_description=description,
        outputs=outputs,
    )


def _preset_colour(tgt: Target, install: Optional[BambuInstall], slot: int) -> str:
    if install is not None:
        sel = install.selection()
        if 0 < slot <= len(sel.filament_colours):
            c = sel.filament_colours[slot - 1]
            if c.startswith("#"):
                return c[:7].upper()
    if tgt and slot - 1 < len(tgt.filaments):
        r = tgt.filaments[slot - 1].resolved or {}
        c = r.get("default_filament_colour")
        c = c[0] if isinstance(c, list) and c else c
        if isinstance(c, str) and c.startswith("#"):
            return c[:7].upper()
    return ""


def _bed_path(output_path: str, bed) -> str:
    base, ext = os.path.splitext(output_path)
    return f"{base}_bed{bed}{ext or '.3mf'}"
