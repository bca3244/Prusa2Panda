"""PrusaSlicer -> Bambu Studio / OrcaSlicer configuration key mapping (DRAFT).

The inverse of Panda2Prusa's ``keymap.py`` + the special cases of its ``translate.py``,
re-verified against the PrintConfig.cpp of each slicer (Bambu Studio master,
OrcaSlicer main, PrusaSlicer 2.9.7). Every enum string a converter can emit is
checked against the target's ``ENUM_ACCEPTED`` table by ``self_check()``.

Each ``Rule`` names the Bambu target key, the PrusaSlicer source key, an optional
converter, and an ``orca`` override when OrcaSlicer uses another key (``orca=None``:
no Orca equivalent; ``bambu=None``: Orca-only rule). Converters take the PrusaSlicer
value (one scalar, as text) and return the target value as text, or ``None`` for
"no sensible value: keep the base preset's".

Percent handling: PrusaSlicer "NN%" values are relative to ``percent_base`` (a Prusa
key, or "nozzle_diameter"/"layer_height"). When the target cannot take a percent (or
takes one over another base) the value is resolved to an absolute number first;
``keep_pct`` lists the flavours ("bambu", "orca") whose key accepts a percent over the
*same* base, so the "%" text is copied verbatim.

Value serialization (vector targets, per-extruder-variant vectors of Bambu Studio 2.x,
nil) is done by ``serialize()`` using the schema of the target flavour.

Usage:
    from reverse_keymap import convert_print, load_schema
    out = convert_print(prusa_cfg_dict, flavor="bambu")   # {bambu_key: value}

``prusa_cfg_dict`` is PrusaSlicer's flat config (Metadata/Slic3r_PE.config): key -> text.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))


def load_schema(name: str):
    """'bambu' | 'orca' | 'prusa' -> generated schema module.

    Imported statically inside the package so that PyInstaller bundles the schemas (an
    import by name or by file path is invisible to it and fails in the frozen exe); the
    file-path fallback is for running this module on its own (tools/gen_schema.py)."""
    if __package__:
        from . import bambu_schema, orca_schema, prusa_schema
        mods = {"bambu": bambu_schema, "orca": orca_schema, "prusa": prusa_schema}
        if name not in mods:
            raise ValueError(f"unknown schema {name!r}")
        return mods[name]
    mod = f"{name}_schema"
    spec = importlib.util.spec_from_file_location(mod, os.path.join(_HERE, mod + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


P = load_schema("prusa")
SAME = "="  # Rule.orca default: Orca uses the Bambu key name


@dataclass(frozen=True)
class Rule:
    bambu: Optional[str]                     # Bambu Studio key (None: Orca-only rule)
    prusa: str                               # PrusaSlicer key
    conv: Optional[Callable[[str], Optional[str]]] = None
    percent_base: Optional[str] = None       # what a Prusa "NN%" is relative to
    keep_pct: tuple = ()                     # flavours whose target takes % over the same base
    quiet: tuple = ()                        # Prusa values that map to "keep base" silently
    note: str = ""
    orca: Optional[str] = SAME               # Orca key; SAME = bambu key; None = no Orca key
    orca_conv: Optional[Callable[[str], Optional[str]]] = SAME  # SAME = use conv

    def target(self, flavor: str) -> Optional[str]:
        if flavor == "orca":
            return self.bambu if self.orca == SAME else self.orca
        return self.bambu

    def converter(self, flavor: str):
        if flavor == "orca" and self.orca_conv is not SAME:
            return self.orca_conv
        return self.conv


# -- value converters -------------------------------------------------------------------

def to_bool(v: str) -> Optional[str]:
    s = v.strip().lower()
    if s in ("1", "true", "yes", "on"):
        return "1"
    if s in ("0", "false", "no", "off", ""):
        return "0"
    return None


def invert_bool(v: str) -> Optional[str]:
    b = to_bool(v)
    return None if b is None else ("0" if b == "1" else "1")


def positive_or_skip(v: str) -> Optional[str]:
    """PrusaSlicer speed 0 = "auto" (limited by max volumetric speed): no Bambu analogue."""
    try:
        return v if float(v.rstrip("%")) > 0 else None
    except ValueError:
        return None


def non_negative(v: str) -> Optional[str]:
    try:
        return v if float(v.rstrip("%")) >= 0 else None
    except ValueError:
        return None


def to_int(v: str) -> Optional[str]:
    try:
        return str(int(round(float(v))))
    except ValueError:
        return None


def enum_map(mapping: dict, passthrough: tuple = ()) -> Callable[[str], Optional[str]]:
    def conv(v: str) -> Optional[str]:
        if v in mapping:
            return mapping[v]
        if v in passthrough:
            return v
        return None
    conv.mapping = mapping          # for self_check()
    conv.passthrough = passthrough
    return conv


def zero_to(value: str) -> Callable[[str], Optional[str]]:
    """Prusa 0 = "auto/off" where the target needs a concrete value."""
    def conv(v: str) -> Optional[str]:
        try:
            return value if float(v.rstrip("%")) == 0 else v
        except ValueError:
            return None
    conv.__name__ = f"zero_to({value})"
    return conv


def compensation_to_shrink(v: str) -> Optional[str]:
    """Prusa filament_shrinkage_compensation_xy 2% (part shrinks 2%) -> Bambu/Orca filament_shrink 98%."""
    try:
        return f"{round(100.0 - float(v.rstrip('%')), 3):g}%"
    except ValueError:
        return None


def abrasive_to_hrc(v: str) -> Optional[str]:
    """Prusa filament_abrasive (bool) -> required_nozzle_HRC. Bambu profiles use 40 for
    abrasive (CF/GF) filaments and 0..3 for the rest; we keep the base for non-abrasive."""
    b = to_bool(v)
    return "40" if b == "1" else None


def arc_fitting_bambu(v: str) -> Optional[str]:
    return {"disabled": "0", "emit_center": "1"}.get(v)


ARC_FITTING_ORCA = enum_map({"disabled": "disabled", "emit_center": "enabled"})

FILAMENT_TYPE_BAMBU = {"PET": "PETG", "FLEX": "TPU", "NYLON": "PA", "EDGE": "PETG", "NGEN": "PETG"}
FILAMENT_TYPE_ORCA = {"NYLON": "PA", "EDGE": "PETG", "NGEN": "PETG"}  # Orca has PET and FLEX


def filament_type_bambu(v: str) -> Optional[str]:
    t = v.strip().strip('"')
    return FILAMENT_TYPE_BAMBU.get(t.upper(), t) or None


def filament_type_orca(v: str) -> Optional[str]:
    t = v.strip().strip('"')
    return FILAMENT_TYPE_ORCA.get(t.upper(), t) or None


# Enum maps: PrusaSlicer value -> target value (verified against s_keys_map_* tables).
_FILL_COMMON = {
    "alignedrectilinear": "alignedrectilinear", "grid": "grid", "triangles": "triangles",
    "stars": "tri-hexagon", "cubic": "cubic", "line": "line", "concentric": "concentric",
    "honeycomb": "honeycomb", "3dhoneycomb": "3dhoneycomb", "gyroid": "gyroid",
    "hilbertcurve": "hilbertcurve", "archimedeanchords": "archimedeanchords",
    "octagramspiral": "octagramspiral", "adaptivecubic": "adaptivecubic",
    "supportcubic": "supportcubic", "lightning": "lightning", "zigzag": "zigzag",
}
FILL_PATTERN_BAMBU = enum_map({**_FILL_COMMON, "rectilinear": "zig-zag",
                               "monotonic": "zig-zag", "monotoniclines": "zig-zag"})
FILL_PATTERN_ORCA = enum_map({**_FILL_COMMON, "rectilinear": "rectilinear",
                              "monotonic": "rectilinear", "monotoniclines": "rectilinear"})
_TOP_COMMON = {"monotonic": "monotonic", "alignedrectilinear": "alignedrectilinear",
               "concentric": "concentric", "hilbertcurve": "hilbertcurve",
               "archimedeanchords": "archimedeanchords", "octagramspiral": "octagramspiral",
               "monotoniclines": "monotonicline"}
# Prusa's parser accepts every InfillPattern for top/bottom (its bottom UI offers the sparse
# ones too); Bambu/Orca parse them but do not offer them in the top/bottom UI list ->
# mapped to monotonic (lossy).
_BOTTOM_EXTRA = {k: "monotonic" for k in _FILL_COMMON if k not in _TOP_COMMON}
TOP_PATTERN_BAMBU = enum_map({**_TOP_COMMON, **_BOTTOM_EXTRA, "rectilinear": "zig-zag"})
TOP_PATTERN_ORCA = enum_map({**_TOP_COMMON, **_BOTTOM_EXTRA, "rectilinear": "rectilinear"})
BOTTOM_PATTERN_BAMBU = enum_map({**_TOP_COMMON, **_BOTTOM_EXTRA, "rectilinear": "zig-zag"})
BOTTOM_PATTERN_ORCA = enum_map({**_TOP_COMMON, **_BOTTOM_EXTRA, "rectilinear": "rectilinear"})

SUPPORT_PATTERN = enum_map({}, passthrough=("rectilinear", "rectilinear-grid", "honeycomb"))
SUPPORT_INTERFACE_PATTERN = enum_map({}, passthrough=("auto", "rectilinear", "concentric"))
SEAM_POSITION = enum_map({"random": "random", "nearest": "nearest", "aligned": "aligned", "rear": "back"})
SCARF_PLACEMENT = enum_map({"nowhere": "none", "contours": "external", "everywhere": "all"})
TOP_ONE_WALL = enum_map({"none": "not apply", "top": "all top", "topmost": "topmost"})
ONLY_ONE_WALL_TOP_ORCA = enum_map({"none": "0", "top": "1", "topmost": "1"})  # Orca: bool (lossy)
FUZZY_SKIN = enum_map({"none": "none", "external": "external", "all": "all"})  # Prusa all = contour+holes
BRIM_TYPE = enum_map({}, passthrough=("no_brim", "outer_only", "inner_only", "outer_and_inner"))
DRAFT_SHIELD_BAMBU = enum_map({}, passthrough=("disabled", "limited", "enabled"))
DRAFT_SHIELD_ORCA = enum_map({"disabled": "disabled", "limited": "enabled", "enabled": "enabled"})
ENSURE_VERTICAL_BAMBU = enum_map({}, passthrough=("disabled", "partial", "enabled"))
ENSURE_VERTICAL_ORCA = enum_map({"disabled": "none", "partial": "ensure_moderate", "enabled": "ensure_all"})
WALL_GENERATOR = enum_map({}, passthrough=("classic", "arachne"))
SLICING_MODE = enum_map({}, passthrough=("regular", "even_odd", "close_holes"))
GCODE_FLAVOR_BAMBU = enum_map({"marlin2": "marlin"}, passthrough=(
    "marlin", "klipper", "reprap", "reprapfirmware", "repetier", "teacup", "makerware",
    "sailfish", "smoothie", "mach3", "machinekit", "no-extrusion"))
GCODE_FLAVOR_ORCA = enum_map({}, passthrough=(
    "marlin", "marlin2", "klipper", "reprap", "reprapfirmware", "repetier", "teacup", "makerware",
    "sailfish", "smoothie", "mach3", "machinekit", "no-extrusion"))
INFILL_RETRACTION_BAMBU = enum_map({"1": "Enabled", "0": "Disabled"})
COOLING_SLOWDOWN_LOGIC = enum_map({"proportional": "uniform_cooling"},  # Prusa-only legacy value
                                  passthrough=("uniform_cooling", "consistent_surface"))
GCODE_LABEL_OBJECTS_ORCA = enum_map({"disabled": "0", "octoprint": "1", "firmware": "1"})
THUMBNAILS_FORMAT = enum_map({}, passthrough=("PNG", "JPG", "QOI"))


# -- print (process) settings ------------------------------------------------------------

PRINT_RULES: List[Rule] = [
    # Layers and perimeters
    Rule("layer_height", "layer_height"),
    Rule("initial_layer_print_height", "first_layer_height", percent_base="layer_height"),
    Rule("wall_loops", "perimeters"),
    Rule("spiral_mode", "spiral_vase", to_bool),
    Rule("slice_closing_radius", "slice_closing_radius"),
    Rule("slicing_mode", "slicing_mode", SLICING_MODE),
    Rule("top_shell_layers", "top_solid_layers"),
    Rule("top_shell_thickness", "top_solid_min_thickness"),
    Rule("bottom_shell_layers", "bottom_solid_layers"),
    Rule("bottom_shell_thickness", "bottom_solid_min_thickness"),
    Rule("ensure_vertical_shell_thickness", "ensure_vertical_shell_thickness", ENSURE_VERTICAL_BAMBU,
         orca_conv=ENSURE_VERTICAL_ORCA, note="Orca uses a 4-level enum; partial->ensure_moderate is approximate"),
    Rule(None, "extra_perimeters_on_overhangs", to_bool, orca="extra_perimeters_on_overhangs"),
    Rule("reduce_crossing_wall", "avoid_crossing_perimeters", to_bool),
    Rule("max_travel_detour_distance", "avoid_crossing_perimeters_max_detour", keep_pct=("bambu", "orca"),
         note="both: mm or % of the direct travel"),
    Rule("detect_thin_wall", "thin_walls", to_bool),
    Rule("detect_overhang_wall", "overhangs", to_bool),
    Rule("seam_position", "seam_position", SEAM_POSITION),
    Rule(None, "staggered_inner_seams", to_bool, orca="staggered_inner_seams"),
    Rule("seam_gap", "seam_gap_distance", non_negative, percent_base="nozzle_diameter", keep_pct=("bambu", "orca"),
         note="Bambu: percent of nozzle only (mm resolved to %, see special case); negative Prusa values dropped"),
    Rule("only_one_wall_first_layer", "only_one_perimeter_first_layer", to_bool),
    Rule("top_one_wall_type", "top_one_perimeter_type", TOP_ONE_WALL, orca="only_one_wall_top",
         orca_conv=ONLY_ONE_WALL_TOP_ORCA, note="Orca only has a bool; 'topmost' becomes 'all top surfaces'"),
    # Scarf seams
    Rule("seam_slope_type", "scarf_seam_placement", SCARF_PLACEMENT),
    Rule("seam_slope_conditional", "scarf_seam_only_on_smooth", to_bool),
    Rule("seam_slope_start_height", "scarf_seam_start_height", percent_base="layer_height", keep_pct=("bambu", "orca")),
    Rule("seam_slope_entire_loop", "scarf_seam_entire_loop", to_bool),
    Rule("seam_slope_min_length", "scarf_seam_length"),
    Rule("seam_slope_inner_walls", "scarf_seam_on_inner_perimeters", to_bool),
    # Infill
    Rule("sparse_infill_density", "fill_density"),
    Rule("sparse_infill_pattern", "fill_pattern", FILL_PATTERN_BAMBU, orca_conv=FILL_PATTERN_ORCA),
    Rule("top_surface_pattern", "top_fill_pattern", TOP_PATTERN_BAMBU, orca_conv=TOP_PATTERN_ORCA),
    Rule("bottom_surface_pattern", "bottom_fill_pattern", BOTTOM_PATTERN_BAMBU, orca_conv=BOTTOM_PATTERN_ORCA),
    Rule("infill_combination", "automatic_infill_combination", to_bool),
    Rule(None, "automatic_infill_combination_max_layer_height", orca="infill_combination_max_layer_height",
         keep_pct=("orca",), percent_base="nozzle_diameter"),
    Rule("infill_direction", "fill_angle"),
    Rule("bridge_angle", "bridge_angle"),
    Rule("minimum_sparse_infill_area", "solid_infill_below_area"),
    Rule("reduce_infill_retraction_mode", "only_retract_when_crossing_perimeters", INFILL_RETRACTION_BAMBU,
         orca="reduce_infill_retraction", orca_conv=to_bool),
    Rule("is_infill_first", "infill_first", to_bool),
    Rule("infill_wall_overlap", "infill_overlap", percent_base="perimeter_extrusion_width", keep_pct=("bambu", "orca")),
    Rule("sparse_infill_anchor", "infill_anchor", percent_base="infill_extrusion_width",
         keep_pct=("bambu", "orca"), orca="infill_anchor"),
    Rule("sparse_infill_anchor_max", "infill_anchor_max", percent_base="infill_extrusion_width",
         keep_pct=("bambu", "orca"), orca="infill_anchor_max"),
    # Ironing (on/off + type: special case)
    Rule("ironing_flow", "ironing_flowrate", keep_pct=("bambu", "orca")),
    Rule("ironing_speed", "ironing_speed"),
    Rule("ironing_spacing", "ironing_spacing"),
    # Fuzzy skin
    Rule("fuzzy_skin", "fuzzy_skin", FUZZY_SKIN),
    Rule("fuzzy_skin_thickness", "fuzzy_skin_thickness"),
    Rule("fuzzy_skin_point_distance", "fuzzy_skin_point_dist"),
    # Speeds (Prusa 0 = auto; Bambu/Orca need a number -> keep base)
    Rule("inner_wall_speed", "perimeter_speed", positive_or_skip),
    Rule("small_perimeter_speed", "small_perimeter_speed", positive_or_skip, percent_base="perimeter_speed",
         note="Prusa % is of perimeter_speed, Bambu/Orca % of outer_wall_speed: resolved to mm/s"),
    Rule("outer_wall_speed", "external_perimeter_speed", positive_or_skip, percent_base="perimeter_speed"),
    Rule("sparse_infill_speed", "infill_speed", positive_or_skip),
    Rule("internal_solid_infill_speed", "solid_infill_speed", positive_or_skip, percent_base="infill_speed"),
    Rule("top_surface_speed", "top_solid_infill_speed", positive_or_skip, percent_base="solid_infill_speed"),
    Rule("support_speed", "support_material_speed", positive_or_skip),
    Rule("support_interface_speed", "support_material_interface_speed", positive_or_skip,
         percent_base="support_material_speed"),
    Rule("bridge_speed", "bridge_speed", positive_or_skip),
    Rule("gap_infill_speed", "gap_fill_speed", positive_or_skip),
    Rule("travel_speed", "travel_speed", positive_or_skip),
    Rule("travel_speed_z", "travel_speed_z", note="0 = use travel speed in all three"),
    Rule("initial_layer_speed", "first_layer_speed", positive_or_skip,
         note="Prusa '%' scales every first-layer speed: special case"),
    Rule("initial_layer_infill_speed", "first_layer_infill_speed", positive_or_skip,
         note="Prusa 0 = use first_layer_speed; % of solid infill speed: special case"),
    Rule("enable_overhang_speed", "enable_dynamic_overhang_speeds", to_bool),
    Rule(None, "max_volumetric_extrusion_rate_slope_positive", orca="max_volumetric_extrusion_rate_slope",
         note="Orca has one slope for both directions; Bambu dropped the feature"),
    # Accelerations (0 = use default in all three)
    Rule("default_acceleration", "default_acceleration"),
    Rule("inner_wall_acceleration", "perimeter_acceleration"),
    Rule("outer_wall_acceleration", "external_perimeter_acceleration"),
    Rule("sparse_infill_acceleration", "infill_acceleration"),
    Rule(None, "solid_infill_acceleration", orca="internal_solid_infill_acceleration"),
    Rule("top_surface_acceleration", "top_solid_infill_acceleration"),
    Rule(None, "bridge_acceleration", orca="bridge_acceleration"),
    Rule("initial_layer_acceleration", "first_layer_acceleration"),
    Rule("travel_acceleration", "travel_acceleration"),
    Rule("travel_short_distance_acceleration", "travel_short_distance_acceleration", orca=None),
    Rule("prime_tower_acceleration", "wipe_tower_acceleration", orca=None),
    # Skirt and brim
    Rule("skirt_loops", "skirts"),
    Rule("skirt_distance", "skirt_distance"),
    Rule("skirt_height", "skirt_height"),
    Rule(None, "min_skirt_length", orca="min_skirt_length"),
    Rule("draft_shield", "draft_shield", DRAFT_SHIELD_BAMBU, orca_conv=DRAFT_SHIELD_ORCA),
    Rule("brim_width", "brim_width"),
    Rule("brim_object_gap", "brim_separation"),
    Rule("brim_type", "brim_type", BRIM_TYPE, note="Bambu default is auto_brim: always write it"),
    # Supports (on/off, auto, style: special case)
    Rule("support_threshold_angle", "support_material_threshold", zero_to("30"), orca_conv=None,
         note="Prusa 0 = automatic (Bambu min 1 -> 30, its default); Orca 0 = use support_threshold_overlap"),
    Rule("enforce_support_layers", "support_material_enforce_layers"),
    Rule("raft_layers", "raft_layers"),
    Rule("raft_first_layer_density", "raft_first_layer_density"),
    Rule("raft_first_layer_expansion", "raft_first_layer_expansion"),
    Rule("raft_contact_distance", "raft_contact_distance"),
    Rule("raft_expansion", "raft_expansion"),
    Rule("support_base_pattern", "support_material_pattern", SUPPORT_PATTERN),
    Rule("support_base_pattern_spacing", "support_material_spacing"),
    Rule("independent_support_layer_height", "support_material_synchronize_layers", invert_bool),
    Rule("support_angle", "support_material_angle"),
    Rule("support_interface_top_layers", "support_material_interface_layers"),
    Rule("support_interface_bottom_layers", "support_material_bottom_interface_layers",
         note="-1 = same as top in all three"),
    Rule("support_interface_pattern", "support_material_interface_pattern", SUPPORT_INTERFACE_PATTERN),
    Rule("support_interface_spacing", "support_material_interface_spacing"),
    Rule("support_interface_loop_pattern", "support_material_interface_contact_loops", to_bool),
    Rule("support_top_z_distance", "support_material_contact_distance"),
    Rule("support_bottom_z_distance", "support_material_bottom_contact_distance",
         note="Prusa 0 = same as top: special case"),
    Rule("support_on_build_plate_only", "support_material_buildplate_only", to_bool),
    Rule("support_object_xy_distance", "support_material_xy_spacing",
         percent_base="external_perimeter_extrusion_width"),
    Rule("tree_support_branch_angle", "support_tree_angle",
         note="Orca organic trees read tree_support_branch_angle_organic too: special case"),
    Rule(None, "support_tree_angle_slow", orca="tree_support_angle_slow"),
    Rule("tree_support_branch_diameter", "support_tree_branch_diameter"),
    Rule("tree_support_branch_diameter_angle", "support_tree_branch_diameter_angle"),
    Rule("tree_support_branch_distance", "support_tree_branch_distance"),
    Rule("tree_support_tip_diameter", "support_tree_tip_diameter"),
    Rule(None, "support_tree_top_rate", orca="tree_support_top_rate", keep_pct=("orca",)),
    Rule("bridge_no_support", "dont_support_bridges", to_bool),
    Rule("thick_bridges", "thick_bridges", to_bool),
    # Multiple extruders (extruder numbers: special case, Orca uses *_filament_id with 0=default)
    Rule("wall_filament", "perimeter_extruder", orca="outer_wall_filament_id",
         note="Orca also has inner_wall_filament_id: write both"),
    Rule("sparse_infill_filament", "infill_extruder", orca="sparse_infill_filament_id"),
    Rule("solid_infill_filament", "solid_infill_extruder", orca="internal_solid_filament_id",
         note="Orca also top_surface_filament_id / bottom_surface_filament_id"),
    Rule("support_filament", "support_material_extruder"),
    Rule("support_interface_filament", "support_material_interface_extruder"),
    Rule(None, "wipe_tower_extruder", orca="wipe_tower_filament"),
    Rule("ooze_prevention", "ooze_prevention", to_bool),
    Rule("standby_temperature_delta", "standby_temperature_delta"),
    Rule("interface_shells", "interface_shells", to_bool),
    Rule("enable_prime_tower", "wipe_tower", to_bool),
    Rule("prime_tower_width", "wipe_tower_width"),
    Rule("prime_tower_brim_width", "wipe_tower_brim_width"),
    Rule("wipe_tower_no_sparse_layers", "wipe_tower_no_sparse_layers", to_bool),
    Rule("prime_tower_infill_gap", "wipe_tower_extra_spacing", orca="wipe_tower_extra_spacing",
         note="Orca keeps Prusa's key (type2 tower); Bambu's infill gap is the closest (both percent, min 100%)"),
    Rule(None, "wipe_tower_bridging", orca="wipe_tower_bridging"),
    Rule(None, "wipe_tower_cone_angle", orca="wipe_tower_cone_angle"),
    Rule(None, "wipe_tower_extra_flow", orca="wipe_tower_extra_flow"),
    Rule("mmu_segmented_region_max_width", "mmu_segmented_region_max_width"),
    Rule("mmu_segmented_region_interlocking_depth", "mmu_segmented_region_interlocking_depth"),
    Rule("interlocking_beam", "interlocking_beam", to_bool),
    Rule("interlocking_beam_width", "interlocking_beam_width"),
    Rule("interlocking_orientation", "interlocking_orientation"),
    Rule("interlocking_beam_layer_count", "interlocking_beam_layer_count"),
    Rule("interlocking_depth", "interlocking_depth"),
    Rule("interlocking_boundary_avoidance", "interlocking_boundary_avoidance"),
    # Extrusion widths: Prusa % is of LAYER HEIGHT (first layer: of first_layer_height) and
    # 0 = auto; Bambu wants mm, Orca mm or % of NOZZLE. Always resolved: special case.
    Rule("line_width", "extrusion_width", percent_base="layer_height"),
    Rule("initial_layer_line_width", "first_layer_extrusion_width", percent_base="first_layer_height"),
    Rule("inner_wall_line_width", "perimeter_extrusion_width", percent_base="layer_height"),
    Rule("outer_wall_line_width", "external_perimeter_extrusion_width", percent_base="layer_height"),
    Rule("sparse_infill_line_width", "infill_extrusion_width", percent_base="layer_height"),
    Rule("internal_solid_infill_line_width", "solid_infill_extrusion_width", percent_base="layer_height"),
    Rule("top_surface_line_width", "top_infill_extrusion_width", percent_base="layer_height"),
    Rule("support_line_width", "support_material_extrusion_width", percent_base="layer_height"),
    # Flow, compensation, resolution
    Rule("bridge_flow", "bridge_flow_ratio"),
    Rule("elefant_foot_compensation", "elefant_foot_compensation"),
    Rule("xy_contour_compensation", "xy_size_compensation",
         note="Prusa offsets the whole slice; also set xy_hole_compensation = -value: special case"),
    Rule("resolution", "gcode_resolution", note="Bambu/Orca 'resolution' is the G-code path simplification "
         "(= Prusa gcode_resolution); Prusa 'resolution' (input mesh simplification) has no equivalent"),
    Rule("enable_arc_fitting", "arc_fitting", arc_fitting_bambu, orca_conv=ARC_FITTING_ORCA),
    Rule("wall_generator", "perimeter_generator", WALL_GENERATOR),
    Rule("wall_transition_length", "wall_transition_length", keep_pct=("bambu", "orca")),
    Rule("wall_transition_filter_deviation", "wall_transition_filter_deviation", keep_pct=("bambu", "orca")),
    Rule("wall_transition_angle", "wall_transition_angle"),
    Rule("wall_distribution_count", "wall_distribution_count"),
    Rule("min_feature_size", "min_feature_size", keep_pct=("bambu", "orca")),
    Rule("min_bead_width", "min_bead_width", keep_pct=("bambu", "orca")),
    # Output
    Rule("filename_format", "output_filename_format",
         note="placeholder names differ ([input_filename_base] works in all three)"),
    Rule("post_process", "post_process"),
    Rule(None, "gcode_comments", to_bool, orca="gcode_comments"),
    Rule(None, "gcode_label_objects", orca="gcode_label_objects", orca_conv=GCODE_LABEL_OBJECTS_ORCA),
    Rule("process_notes", "notes", orca="notes"),
    Rule(None, "toolchange_ordering", orca="toolchange_ordering",
         orca_conv=enum_map({"optimized": "default", "cyclic": "cyclic"})),
    Rule("print_in_clockwise", "prefer_clockwise_movements", to_bool, orca="wall_direction",
         orca_conv=enum_map({"1": "cw", "0": "ccw"}),
         note="Prusa: printer key; Bambu (hidden, comDevelop) / Orca: process keys"),
    Rule("print_sequence", "complete_objects", enum_map({"1": "by object", "0": "by layer"})),
]

# PrusaSlicer "speed for N% overlap" slots -> Bambu overhang bands (named by overhang
# amount; 4/4 = 75-100 % of the line width overhanging). Order flips. Prusa % values
# are of external_perimeter_speed; Orca's % is of outer_wall_speed (same base, kept);
# Bambu needs mm/s. Prusa 0 = no slowdown == Bambu/Orca 0.
OVERHANG_SPEED_RULES = [
    # (prusa, bambu, orca)
    ("overhang_speed_0", "overhang_totally_speed", "overhang_4_4_speed"),
    ("overhang_speed_1", "overhang_4_4_speed", None),  # Orca: folded into 4/4 (min of 0 and 1)
    ("overhang_speed_2", "overhang_3_4_speed", "overhang_3_4_speed"),
    ("overhang_speed_3", "overhang_2_4_speed", "overhang_2_4_speed"),
]


# -- filament settings (one value per filament) ------------------------------------------

FILAMENT_RULES: List[Rule] = [
    Rule("filament_colour", "filament_colour"),
    Rule("filament_diameter", "filament_diameter"),
    Rule("filament_type", "filament_type", filament_type_bambu, orca_conv=filament_type_orca),
    Rule("filament_soluble", "filament_soluble", to_bool),
    Rule("required_nozzle_HRC", "filament_abrasive", abrasive_to_hrc),
    Rule("filament_notes", "filament_notes", note="Bambu: one scalar string for the preset; Orca: vector"),
    Rule("filament_max_volumetric_speed", "filament_max_volumetric_speed", positive_or_skip,
         note="Prusa 0 = unlimited; Bambu/Orca need > 0. Also min() with print max_volumetric_speed"),
    Rule("filament_flow_ratio", "extrusion_multiplier"),
    Rule("filament_density", "filament_density"),
    Rule("filament_cost", "filament_cost"),
    Rule("filament_minimal_purge_on_wipe_tower", "filament_minimal_purge_on_wipe_tower"),
    Rule("nozzle_temperature", "temperature"),
    Rule("nozzle_temperature_initial_layer", "first_layer_temperature"),
    # bed temperatures fan out to every plate type: special case
    Rule("chamber_temperatures", "chamber_temperature", orca="chamber_temperature"),
    Rule(None, "chamber_minimal_temperature", orca="chamber_minimal_temperature"),
    Rule(None, "idle_temperature", orca="idle_temperature", note="Prusa nil = off; Orca 0 = off"),
    Rule("reduce_fan_stop_start_freq", "fan_always_on", to_bool),
    Rule("slow_down_for_layer_cooling", "cooling", to_bool,
         note="Prusa 'cooling' also gates the fan logic; Bambu/Orca only the slowdown"),
    Rule("cooling_slowdown_logic", "cooling_slowdown_logic", COOLING_SLOWDOWN_LOGIC, orca=None),
    Rule("cooling_perimeter_transition_distance", "cooling_perimeter_transition_distance", orca=None),
    Rule("fan_min_speed", "min_fan_speed"),
    Rule("fan_max_speed", "max_fan_speed"),
    Rule("overhang_fan_speed", "bridge_fan_speed"),
    Rule("close_fan_the_first_x_layers", "disable_fan_first_layers"),
    Rule("full_fan_speed_layer", "full_fan_speed_layer"),
    Rule("fan_cooling_layer_time", "fan_below_layer_time"),
    Rule("slow_down_layer_time", "slowdown_below_layer_time"),
    Rule("slow_down_min_speed", "min_print_speed"),
    Rule("filament_start_gcode", "start_filament_gcode", note="macro placeholders differ"),
    Rule("filament_end_gcode", "end_filament_gcode", note="macro placeholders differ"),
    Rule("filament_shrink", "filament_shrinkage_compensation_xy", compensation_to_shrink),
    Rule(None, "filament_shrinkage_compensation_z", compensation_to_shrink, orca="filament_shrinkage_compensation_z"),
    # Per-filament overrides of the printer's retraction (nil = no override, both sides)
    Rule("filament_retraction_length", "filament_retract_length"),
    Rule("filament_z_hop", "filament_retract_lift"),
    Rule("filament_retract_lift_above", "filament_retract_lift_above"),
    Rule("filament_retract_lift_below", "filament_retract_lift_below"),
    Rule("filament_retraction_speed", "filament_retract_speed"),
    Rule("filament_deretraction_speed", "filament_deretract_speed"),
    Rule("filament_retract_restart_extra", "filament_retract_restart_extra"),
    Rule("filament_retraction_minimum_travel", "filament_retract_before_travel"),
    Rule("filament_retract_when_changing_layer", "filament_retract_layer_change"),
    Rule("filament_wipe", "filament_wipe"),
    Rule("filament_retract_before_wipe", "filament_retract_before_wipe"),
    Rule("filament_retract_length_toolchange", "filament_retract_length_toolchange"),
    Rule("filament_retract_restart_extra_toolchange", "filament_retract_restart_extra_toolchange"),
    # Prusa MMU (single extruder multi material) ramming/cooling: Orca kept Prusa's
    # wipe tower ("type2") and these keys; Bambu has none of them.
    *[Rule(None, k, orca=k) for k in (
        "filament_cooling_final_speed", "filament_cooling_initial_speed", "filament_cooling_moves",
        "filament_loading_speed", "filament_loading_speed_start", "filament_unloading_speed",
        "filament_unloading_speed_start", "filament_toolchange_delay", "filament_ramming_parameters",
        "filament_multitool_ramming", "filament_multitool_ramming_flow", "filament_multitool_ramming_volume",
        "filament_stamping_distance", "filament_stamping_loading_speed")],
]

# Filament keys whose Bambu target is a *printer* scalar (PrusaSlicer: per filament).
FILAMENT_TO_PRINTER_RULES = [
    Rule("machine_load_filament_time", "filament_load_time", note="take the max over used filaments"),
    Rule("machine_unload_filament_time", "filament_unload_time", note="take the max over used filaments"),
]


# -- printer settings -----------------------------------------------------------------------

PRINTER_RULES: List[Rule] = [
    Rule("printable_area", "bed_shape"),
    Rule("printable_height", "max_print_height"),
    Rule("bed_custom_texture", "bed_custom_texture"),
    Rule("bed_custom_model", "bed_custom_model"),
    Rule("gcode_flavor", "gcode_flavor", GCODE_FLAVOR_BAMBU, orca_conv=GCODE_FLAVOR_ORCA,
         note="Bambu UI offers marlin/klipper only but parses all; marlin2 -> marlin for Bambu"),
    Rule("use_relative_e_distances", "use_relative_e_distances", to_bool),
    Rule("use_firmware_retraction", "use_firmware_retraction", to_bool),
    Rule("single_extruder_multi_material", "single_extruder_multi_material", to_bool),
    Rule("printer_model", "printer_model", note="Bambu keys system behaviour on its own model names: usually leave unset"),
    Rule("printer_variant", "printer_variant"),
    Rule("printer_notes", "printer_notes"),
    Rule("silent_mode", "silent_mode", to_bool, orca=None),
    Rule("extruder_clearance_max_radius", "extruder_clearance_radius", orca="extruder_clearance_radius"),
    Rule("extruder_clearance_height_to_rod", "extruder_clearance_height",
         note="also extruder_clearance_height_to_lid = same value"),
    Rule("template_custom_gcode", "template_custom_gcode"),
    Rule("machine_pause_gcode", "pause_print_gcode"),
    Rule("machine_start_gcode", "start_gcode", note="placeholders differ: see GCODE_PLACEHOLDERS"),
    Rule("machine_end_gcode", "end_gcode", note="placeholders differ"),
    Rule("before_layer_change_gcode", "before_layer_gcode"),
    Rule("layer_change_gcode", "layer_gcode"),
    Rule("change_filament_gcode", "toolchange_gcode"),
    Rule("printing_by_object_gcode", "between_objects_gcode"),
    Rule(None, "z_offset", orca="z_offset"),
    Rule(None, "thumbnails", orca="thumbnails", note="same 'WxH/FMT,...' syntax"),
    Rule(None, "high_current_on_filament_swap", to_bool, orca="high_current_on_filament_swap"),
    Rule(None, "parking_pos_retraction", orca="parking_pos_retraction"),
    Rule(None, "cooling_tube_length", orca="cooling_tube_length"),
    Rule(None, "cooling_tube_retraction", orca="cooling_tube_retraction"),
    Rule(None, "extra_loading_move", orca="extra_loading_move"),
    Rule(None, "thumbnails_format", orca="thumbnails_format", orca_conv=THUMBNAILS_FORMAT),
    Rule(None, "single_extruder_multi_material_priming", to_bool, orca="single_extruder_multi_material_priming"),
    Rule(None, "remaining_times", invert_bool, orca="disable_m73", note="Bambu always emits M73"),
    Rule(None, "machine_limits_usage", orca="emit_machine_limits_to_gcode",
         orca_conv=enum_map({"emit_to_gcode": "1", "time_estimate_only": "0", "ignore": "0"})),
    Rule(None, "multimaterial_purging", orca="prime_volume", note="Bambu: filament_prime_volume per filament"),
    Rule("default_print_profile", "default_print_profile"),
]

# Per-extruder printer vectors.
EXTRUDER_RULES: List[Rule] = [
    Rule("nozzle_diameter", "nozzle_diameter"),
    Rule("min_layer_height", "min_layer_height"),
    Rule("max_layer_height", "max_layer_height", note="Prusa 0 = 75% of nozzle: special case"),
    Rule("extruder_offset", "extruder_offset"),
    Rule("extruder_colour", "extruder_colour"),
    Rule("retraction_length", "retract_length"),
    Rule("z_hop", "retract_lift"),
    Rule("retract_lift_above", "retract_lift_above"),
    Rule("retract_lift_below", "retract_lift_below"),
    Rule("retraction_speed", "retract_speed"),
    Rule("deretraction_speed", "deretract_speed", note="0 = same as retraction speed in all three"),
    Rule("retract_before_wipe", "retract_before_wipe"),
    Rule("retract_restart_extra", "retract_restart_extra"),
    Rule("retraction_minimum_travel", "retract_before_travel"),
    Rule("wipe", "wipe", to_bool),
    Rule("retract_when_changing_layer", "retract_layer_change", to_bool),
    Rule("retract_length_toolchange", "retract_length_toolchange"),
    Rule("retract_restart_extra_toolchange", "retract_restart_extra_toolchange"),
    Rule(None, "travel_slope", orca="travel_slope", note="Orca min 1 deg; only meaningful with Slope Lift"),
]

MACHINE_LIMIT_RULES = [  # (prusa, bambu==orca); pairs of [normal, silent]
    ("machine_max_acceleration_x", "machine_max_acceleration_x"),
    ("machine_max_acceleration_y", "machine_max_acceleration_y"),
    ("machine_max_acceleration_z", "machine_max_acceleration_z"),
    ("machine_max_acceleration_e", "machine_max_acceleration_e"),
    ("machine_max_acceleration_extruding", "machine_max_acceleration_extruding"),
    ("machine_max_acceleration_retracting", "machine_max_acceleration_retracting"),
    ("machine_max_acceleration_travel", "machine_max_acceleration_travel"),
    ("machine_max_feedrate_x", "machine_max_speed_x"),
    ("machine_max_feedrate_y", "machine_max_speed_y"),
    ("machine_max_feedrate_z", "machine_max_speed_z"),
    ("machine_max_feedrate_e", "machine_max_speed_e"),
    ("machine_max_jerk_x", "machine_max_jerk_x"),
    ("machine_max_jerk_y", "machine_max_jerk_y"),
    ("machine_max_jerk_z", "machine_max_jerk_z"),
    ("machine_max_jerk_e", "machine_max_jerk_e"),
    ("machine_min_extruding_rate", "machine_min_extruding_rate"),
    ("machine_min_travel_rate", "machine_min_travel_rate"),
]
ORCA_MACHINE_LIMIT_RULES = [("machine_max_junction_deviation", "machine_max_junction_deviation")]

# Bambu filament plate-temperature keys (per BedType, PrintConfig.hpp get_bed_temp_key).
BED_TEMP_KEYS = {
    "Cool Plate": ("cool_plate_temp", "cool_plate_temp_initial_layer"),
    "Engineering Plate": ("eng_plate_temp", "eng_plate_temp_initial_layer"),
    "High Temp Plate": ("hot_plate_temp", "hot_plate_temp_initial_layer"),
    "Textured PEI Plate": ("textured_plate_temp", "textured_plate_temp_initial_layer"),
    "Supertack Plate": ("supertack_plate_temp", "supertack_plate_temp_initial_layer"),
}
BED_TEMP_KEYS_ORCA = {**BED_TEMP_KEYS,
                      "Textured Cool Plate": ("textured_cool_plate_temp", "textured_cool_plate_temp_initial_layer")}
DEFAULT_BED_TYPE = "Textured PEI Plate"  # PrusaSlicer has no bed type; satin/textured sheets ~ PEI

# Common G-code placeholder renames for start/end/layer macros (PrusaSlicer -> Bambu/Orca).
# Bambu has no [first_layer_*] names; Orca keeps many Prusa aliases. Not exhaustive.
GCODE_PLACEHOLDERS = {
    "first_layer_temperature": "nozzle_temperature_initial_layer",
    "first_layer_bed_temperature": "bed_temperature_initial_layer_single",
    "bed_temperature": "bed_temperature",  # Bambu: vector per filament (current plate)
    "temperature": "nozzle_temperature",
    "layer_num": "layer_num",
    "layer_z": "layer_z",
    "max_layer_z": "max_layer_z",
    "first_layer_print_min": "first_layer_print_min",
    "first_layer_print_max": "first_layer_print_max",
    "filament_type": "filament_type",
    "initial_tool": "initial_tool",
    "next_extruder": "next_extruder",
    "previous_extruder": "previous_extruder",
}


# -- conversion engine ------------------------------------------------------------------------

def prusa_split(key: str, raw: Optional[str]) -> Optional[list]:
    """Split a PrusaSlicer ini vector value into elements."""
    if raw is None:
        return None
    kind = P.OPTIONS.get(key, ("string",))[0]
    if kind == "strings":
        out, cur, q, i = [], "", False, 0
        while i < len(raw):
            c = raw[i]
            if c == '"' and (i == 0 or raw[i - 1] != "\\"):
                q = not q
            elif c == ";" and not q:
                out.append(cur)
                cur = ""
            else:
                cur += c
            i += 1
        out.append(cur)
        return [unescape_cstyle(s) for s in out]
    if kind.endswith("s") and kind not in ("string",):
        return [s.strip() for s in raw.split(",")]
    return [raw]


def unescape_cstyle(s: str) -> str:
    """Inverse of PrusaSlicer's escape_string_cstyle (\\n, \\r, \\\\, \\")."""
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append({"n": "\n", "r": "\r", "t": "\t"}.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _num(v) -> Optional[float]:
    try:
        return float(str(v).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


class PrusaView:
    """Read access to a flat PrusaSlicer config with percent resolution."""

    def __init__(self, cfg: Dict[str, str], extruder: int = 0):
        self.cfg = cfg
        self.extruder = extruder  # index for per-extruder bases (nozzle diameter)

    def has(self, k: str) -> bool:
        return k in self.cfg

    def raw(self, k: str) -> Optional[str]:
        if k in self.cfg:
            return self.cfg[k]
        d = P.DEFAULTS.get(k)
        return d

    def scalar(self, k: str) -> Optional[str]:
        v = self.raw(k)
        if v is None:
            return None
        kind = P.OPTIONS.get(k, ("string",))[0]
        if kind.endswith("s") and kind != "string":
            parts = prusa_split(k, v) or [None]
            return parts[min(self.extruder, len(parts) - 1)]
        if kind == "string":
            return unescape_cstyle(v)
        return v

    def absolute(self, k: str, depth: int = 0) -> Optional[float]:
        """Value of k in absolute units, following Prusa's percent bases."""
        if k == "nozzle_diameter":
            return _num(self.scalar("nozzle_diameter"))
        v = self.scalar(k)
        if v is None:
            return None
        if not str(v).strip().endswith("%"):
            n = _num(v)
            if n == 0 and k in _ZERO_MEANS:
                return self.absolute(_ZERO_MEANS[k], depth + 1) if depth < 6 else None
            return n
        base = _PERCENT_BASE.get(k) or P.RATIO_OVER.get(k)
        if not base or depth > 6:
            return None
        b = self.absolute(base, depth + 1)
        return None if b is None else b * _num(v) / 100.0


# Prusa keys where 0 means "use that other key" (for absolute() resolution).
_ZERO_MEANS = {
    "first_layer_extrusion_width": "extrusion_width",
    "perimeter_extrusion_width": "extrusion_width",
    "external_perimeter_extrusion_width": "extrusion_width",
    "infill_extrusion_width": "extrusion_width",
    "solid_infill_extrusion_width": "extrusion_width",
    "top_infill_extrusion_width": "extrusion_width",
    "support_material_extrusion_width": "extrusion_width",
    "support_material_bottom_contact_distance": "support_material_contact_distance",
}
_PERCENT_BASE = {  # Prusa bases not declared via def->ratio_over
    "extrusion_width": "layer_height",
    "perimeter_extrusion_width": "layer_height",
    "external_perimeter_extrusion_width": "layer_height",
    "infill_extrusion_width": "layer_height",
    "solid_infill_extrusion_width": "layer_height",
    "top_infill_extrusion_width": "layer_height",
    "support_material_extrusion_width": "layer_height",
    "seam_gap_distance": "nozzle_diameter",
    "automatic_infill_combination_max_layer_height": "nozzle_diameter",
    "overhang_speed_0": "external_perimeter_speed",
    "overhang_speed_1": "external_perimeter_speed",
    "overhang_speed_2": "external_perimeter_speed",
    "overhang_speed_3": "external_perimeter_speed",
    "first_layer_infill_speed": "solid_infill_speed",
}
# Flow::auto_extrusion_width() of PrusaSlicer 2.9.7 (width 0 and extrusion_width 0).
AUTO_WIDTH_FACTOR = {
    "external_perimeter_extrusion_width": 1.125, "perimeter_extrusion_width": 1.125,
    "infill_extrusion_width": 1.125, "solid_infill_extrusion_width": 1.125,
    "top_infill_extrusion_width": 1.0, "support_material_extrusion_width": 1.0,
    "first_layer_extrusion_width": 1.125, "extrusion_width": 1.125,
}


def apply_rule(rule: Rule, flavor: str, value: Optional[str], pv: PrusaView,
               warnings: list) -> Optional[str]:
    """One Prusa scalar -> target scalar text for rule (no vector wrapping)."""
    if value is None:
        return None
    is_text = P.OPTIONS.get(rule.prusa, ("string",))[0] in ("string", "strings")
    v = value if is_text else value.strip()
    if v == "nil":
        return "nil"
    pct = v.endswith("%")
    if pct and flavor not in rule.keep_pct and rule.percent_base:
        tgt_schema = load_schema(flavor)
        tkind = tgt_schema.OPTIONS.get(rule.target(flavor), ("float",))[0]
        if not tkind.startswith("percent"):
            a = pv.absolute(rule.prusa)
            if a is None:
                warnings.append(f"{rule.prusa}={v!r}: cannot resolve percent; kept base value")
                return None
            v = f"{round(a, 4):g}"
    conv = rule.converter(flavor)
    if conv is not None:
        out = conv(v)
        if out is None:
            if v not in rule.quiet and v.strip() not in ("", "0"):
                warnings.append(f"{rule.prusa}={v!r} has no {flavor} equivalent for {rule.target(flavor)}; kept base")
            return None
        v = out
    return v


def serialize(schema, key: str, value, n: int = 1):
    """Scalar text / list -> what project_settings.config JSON holds for key."""
    kind = schema.OPTIONS.get(key, ("string",))[0]
    if kind in ("points",):
        return list(value) if isinstance(value, list) else [value]
    if kind.endswith("s") and kind != "string" or kind == "pointsgroups":
        # Only per-extruder-variant vectors repeat once per variant; any other vector
        # (post_process, ...) is one value, not n copies of it.
        per_variant = (key in getattr(schema, "PRINT_VARIANT_KEYS", ()) or
                       key in getattr(schema, "FILAMENT_VARIANT_KEYS", ()) or
                       key in getattr(schema, "PRINTER_VARIANT_KEYS", ()))
        vals = value if isinstance(value, list) else [value] * (max(1, n) if per_variant else 1)
        if kind == "bools":
            vals = [to_bool(x) or x for x in vals]
        return [str(x) for x in vals]
    return str(value[0] if isinstance(value, list) else value)


def _rules_for(rules, flavor):
    return [r for r in rules if r.target(flavor)]


def convert_print(cfg: Dict[str, str], flavor: str = "bambu", warnings: Optional[list] = None,
                  variants: int = 1) -> Dict[str, object]:
    """Prusa print settings -> {target key: serialized value} for 'bambu' or 'orca'.

    ``variants``: length of per-extruder-variant vectors in the target printer preset
    (Bambu Studio 2.x stores most speeds/accels as vectors, one per extruder variant)."""
    S = load_schema(flavor)
    warnings = [] if warnings is None else warnings
    pv = PrusaView(cfg)
    out: Dict[str, object] = {}
    for rule in _rules_for(PRINT_RULES, flavor):
        if not pv.has(rule.prusa):
            continue
        tkey = rule.target(flavor)
        if tkey not in S.OPTIONS:
            continue
        v = apply_rule(rule, flavor, pv.scalar(rule.prusa), pv, warnings)
        if v is None:
            continue
        out[tkey] = serialize(S, tkey, v, variants)
    out.update(special_print(pv, flavor, warnings, variants))
    if "post_process" in out:  # every script, once each; none when empty
        scripts = [x for x in (prusa_split("post_process", pv.raw("post_process") or "") or []) if x and x.strip()]
        out["post_process"] = scripts
    # Prusa region extruders are >= 1 and "1" at print level normally means "the object's
    # extruder"; Bambu/Orca use 0 for that (Orca's legacy handler does the same rewrite).
    # Other numbers still need remapping to filament slots by the caller.
    for rule in PRINT_RULES:
        if rule.prusa in ("perimeter_extruder", "infill_extruder", "solid_infill_extruder"):
            t = rule.target(flavor)
            if t in out and out[t] == "1":
                out[t] = "0"
    return out


def _n_filaments(pv: PrusaView) -> int:
    return max(1, len(prusa_split("filament_colour", pv.raw("filament_colour")) or [""]),
               len(prusa_split("nozzle_diameter", pv.raw("nozzle_diameter")) or [""]))


def convert_filament(cfg: Dict[str, str], flavor: str = "bambu", warnings: Optional[list] = None,
                     n: Optional[int] = None) -> Dict[str, object]:
    """Prusa per-filament vectors -> target per-filament vectors (one entry per filament)."""
    S = load_schema(flavor)
    warnings = [] if warnings is None else warnings
    n = n or _n_filaments(PrusaView(cfg))
    views = [PrusaView(cfg, extruder=i) for i in range(n)]
    out: Dict[str, object] = {}
    for rule in _rules_for(FILAMENT_RULES, flavor):
        tkey = rule.target(flavor)
        if not views[0].has(rule.prusa) or tkey not in S.OPTIONS:
            continue
        vals = [apply_rule(rule, flavor, pv.scalar(rule.prusa), pv, warnings) for pv in views]
        base = S.DEFAULTS.get(tkey)
        fill = (base[0] if isinstance(base, list) and base else base) if base is not None else None
        vals = [v if v is not None else fill for v in vals]
        if any(v is None for v in vals):
            continue
        if S.OPTIONS[tkey][0] in ("string",):
            out[tkey] = vals[0]           # e.g. Bambu filament_notes is one string per preset
        else:
            out[tkey] = serialize(S, tkey, vals)
    # Bed temperatures fan out to every plate type.
    bed = [pv.scalar("bed_temperature") or "60" for pv in views]
    first = [pv.scalar("first_layer_bed_temperature") or b for pv, b in zip(views, bed)]
    out.update(bed_temperature_values(bed, first, flavor))
    # Keep the nozzle temperature inside the filament's recommended range (Bambu warns otherwise).
    lo = S.DEFAULTS.get("nozzle_temperature_range_low", ["190"])[0]
    hi = S.DEFAULTS.get("nozzle_temperature_range_high", ["240"])[0]
    temps = [[_num(pv.scalar("temperature")), _num(pv.scalar("first_layer_temperature"))] for pv in views]
    out["nozzle_temperature_range_low"] = [f"{min([float(lo)] + [t for t in ts if t]):g}" for ts in temps]
    out["nozzle_temperature_range_high"] = [f"{max([float(hi)] + [t for t in ts if t]):g}" for ts in temps]
    # filament_max_volumetric_speed: Prusa 0 = unlimited; the print-level max_volumetric_speed
    # (0 = off) caps it. Bambu/Orca need > 0: keep the base preset's when both are 0.
    mvs = _num(views[0].scalar("max_volumetric_speed")) or None
    fmvs = [_num(pv.scalar("filament_max_volumetric_speed")) or None for pv in views]
    caps = [min([x for x in (mvs, f) if x]) if (mvs or f) else None for f in fmvs]
    if all(c is not None for c in caps):
        out["filament_max_volumetric_speed"] = [f"{c:g}" for c in caps]
    else:
        out.pop("filament_max_volumetric_speed", None)
    # Ramping (slope) travel lift as a per-filament override.
    ramp = prusa_split("filament_travel_ramping_lift", views[0].raw("filament_travel_ramping_lift"))
    if ramp and "filament_z_hop_types" in S.OPTIONS:
        ramp = (ramp + [ramp[-1]] * n)[:n]
        out["filament_z_hop_types"] = ["nil" if r == "nil" else ("Slope Lift" if to_bool(r) == "1" else "Normal Lift")
                                       for r in ramp]
    vendor = views[0].scalar("filament_vendor")
    if vendor and "filament_vendor" in S.OPTIONS:
        out["filament_vendor"] = [vendor] * n  # Prusa: one per preset; Bambu/Orca: per filament
    ids = prusa_split("filament_settings_id", views[0].raw("filament_settings_id")) or []
    if ids:
        out["filament_settings_id"] = (ids + [ids[-1]] * n)[:n]
    return out


def convert_printer(cfg: Dict[str, str], flavor: str = "bambu", warnings: Optional[list] = None) -> Dict[str, object]:
    """Prusa printer settings -> target machine settings (scalars + per-extruder vectors)."""
    S = load_schema(flavor)
    warnings = [] if warnings is None else warnings
    pv0 = PrusaView(cfg)
    n = max(1, len(prusa_split("nozzle_diameter", pv0.raw("nozzle_diameter")) or [""]))
    views = [PrusaView(cfg, extruder=i) for i in range(n)]
    out: Dict[str, object] = {}
    for rule in _rules_for(PRINTER_RULES, flavor):
        tkey = rule.target(flavor)
        if not pv0.has(rule.prusa) or tkey not in S.OPTIONS:
            continue
        if rule.prusa == "bed_shape":
            out[tkey] = prusa_split("bed_shape", pv0.raw("bed_shape"))
            continue
        v = apply_rule(rule, flavor, pv0.scalar(rule.prusa), pv0, warnings)
        if v is not None:
            out[tkey] = serialize(S, tkey, v)
    for rule in _rules_for(EXTRUDER_RULES, flavor):
        tkey = rule.target(flavor)
        if not pv0.has(rule.prusa) or tkey not in S.OPTIONS:
            continue
        vals = [apply_rule(rule, flavor, pv.scalar(rule.prusa), pv, warnings) for pv in views]
        if all(v is not None for v in vals):
            out[tkey] = serialize(S, tkey, vals)
    # max_layer_height 0 = 75% of nozzle in Prusa.
    if "max_layer_height" in out:
        nd = [_num(pv.scalar("nozzle_diameter")) or 0.4 for pv in views]
        out["max_layer_height"] = [x if _num(x) else f"{0.75 * d:g}" for x, d in zip(out["max_layer_height"], nd)]
    for pk, tk in MACHINE_LIMIT_RULES + (ORCA_MACHINE_LIMIT_RULES if flavor == "orca" else []):
        if pv0.has(pk) and tk in S.OPTIONS:
            out[tk] = prusa_split(pk, pv0.raw(pk))
    out["z_hop_types"] = (z_hop_types(cfg) + ["Normal Lift"] * n)[:n]
    if "extruder_clearance_height" in cfg:
        out["extruder_clearance_height_to_lid"] = cfg["extruder_clearance_height"]
    for rule in FILAMENT_TO_PRINTER_RULES:
        vals = [_num(x) for x in (prusa_split(rule.prusa, pv0.raw(rule.prusa)) or [])]
        vals = [x for x in vals if x is not None]
        if vals and rule.target(flavor) in S.OPTIONS:
            out[rule.target(flavor)] = f"{max(vals):g}"
    return out


# -- special cases ------------------------------------------------------------------------------

def support_values(pv: PrusaView, flavor: str) -> dict:
    """support_material / _auto / _style -> enable_support, support_type, support_style."""
    out = {}
    on = to_bool(pv.scalar("support_material") or "0")
    auto = to_bool(pv.scalar("support_material_auto") or "1")
    style = pv.scalar("support_material_style") or "grid"
    out["enable_support"] = on or "0"
    tree = style in ("organic", "tree")
    out["support_type"] = ("tree" if tree else "normal") + ("(auto)" if auto == "1" else "(manual)")
    if tree:
        out["support_style"] = "organic" if flavor == "orca" else "tree_organic"
    else:
        out["support_style"] = {"grid": "grid", "snug": "snug"}.get(style, "default")
    return out


def ironing_values(pv: PrusaView) -> dict:
    if to_bool(pv.scalar("ironing") or "0") != "1":
        return {"ironing_type": "no ironing"}
    t = pv.scalar("ironing_type") or "top"
    return {"ironing_type": t if t in ("top", "topmost", "solid") else "top"}


def special_print(pv: PrusaView, flavor: str, warnings: list, variants: int = 1) -> dict:
    S = load_schema(flavor)
    out: Dict[str, object] = {}

    def put(k, v):
        if k in S.OPTIONS and v is not None:
            out[k] = serialize(S, k, v, variants)

    for k, v in support_values(pv, flavor).items():
        put(k, v)
    # PrusaSlicer prints no brim when brim_width is 0, whatever brim_type says; Bambu / Orca
    # read brim_type alone (and default to auto_brim), so say no_brim outright.
    if pv.has("brim_width") or pv.has("brim_type"):
        try:
            width = float(pv.scalar("brim_width") or 0)
        except ValueError:
            width = 0.0
        if width <= 0:
            put("brim_type", "no_brim")
    for k, v in ironing_values(pv).items():
        put(k, v)
    if pv.has("external_perimeters_first"):
        put("wall_sequence", "outer wall/inner wall" if to_bool(pv.scalar("external_perimeters_first")) == "1"
            else "inner wall/outer wall")
    # Bottom contact distance 0 = "same as top" in Prusa only.
    if pv.has("support_material_bottom_contact_distance") and _num(pv.scalar("support_material_bottom_contact_distance")) == 0:
        put("support_bottom_z_distance", pv.scalar("support_material_contact_distance"))
    # Hole compensation: Prusa xy_size_compensation offsets holes inward too.
    xy = _num(pv.scalar("xy_size_compensation"))
    if xy:
        put("xy_hole_compensation", f"{-xy:g}")
    # Seam gap in mm -> Bambu % of nozzle diameter.
    sg = pv.scalar("seam_gap_distance")
    if flavor == "bambu" and sg and not sg.endswith("%"):
        nd = _num(pv.scalar("nozzle_diameter"))
        if nd and _num(sg) is not None and _num(sg) >= 0:
            put("seam_gap", f"{round(100 * _num(sg) / nd, 2):g}%")
    # Overhang speed slots.
    for prusa_k, bambu_k, orca_k in OVERHANG_SPEED_RULES:
        if not pv.has(prusa_k):
            continue
        v = pv.scalar(prusa_k)
        if flavor == "orca":
            if prusa_k == "overhang_speed_0":
                a0, a1 = pv.absolute("overhang_speed_0"), pv.absolute("overhang_speed_1")
                cand = [x for x in (a0, a1) if x]
                if cand:
                    put("overhang_4_4_speed", f"{min(cand):g}")
            elif orca_k:
                put(orca_k, v)  # same % base (outer wall speed)
        else:
            a = pv.absolute(prusa_k) if v.endswith("%") else _num(v)
            if a is not None:
                put(bambu_k, f"{a:g}")
    # infill_every_layers > 1 -> combine infill (Bambu: bool; Orca: + max height).
    iel = _num(pv.scalar("infill_every_layers"))
    if iel and iel > 1 and to_bool(pv.scalar("automatic_infill_combination") or "0") != "1":
        put("infill_combination", "1")
        if flavor == "orca":
            lh = _num(pv.scalar("layer_height")) or 0.2
            put("infill_combination_max_layer_height", f"{round(iel * lh, 3):g}")
        warnings.append(f"infill_every_layers={iel:g}: mapped to infill_combination (layer count not preserved)")
    # Gap fill on/off.
    if pv.has("gap_fill_enabled"):
        on = to_bool(pv.scalar("gap_fill_enabled")) == "1"
        if flavor == "orca":
            put("gap_fill_target", "everywhere" if on else "nowhere")
        elif not on:
            warnings.append("gap_fill_enabled=0: Bambu Studio cannot disable gap fill")
    # First-layer speed given as % scales everything in Prusa.
    fls = pv.scalar("first_layer_speed")
    if fls and fls.endswith("%"):
        a = pv.absolute("perimeter_speed")
        if a:
            put("initial_layer_speed", f"{a * _num(fls) / 100:g}")
            warnings.append(f"first_layer_speed={fls}: Bambu/Orca take mm/s; used {fls} of perimeter_speed")
    fli = pv.scalar("first_layer_infill_speed")
    if fli is not None and _num(fli) == 0:
        put("initial_layer_infill_speed", out.get("initial_layer_speed") and
            (out["initial_layer_speed"][0] if isinstance(out["initial_layer_speed"], list) else out["initial_layer_speed"]))
    # Extrusion widths: resolve Prusa % (of layer height) and 0 (auto) to mm.
    nd = _num(pv.scalar("nozzle_diameter")) or 0.4
    for rule in PRINT_RULES:
        if not (rule.prusa.endswith("extrusion_width") and rule.target(flavor)):
            continue
        raw = pv.scalar(rule.prusa)
        if raw is None:
            continue
        a = pv.absolute(rule.prusa)
        if not a:
            a = nd * AUTO_WIDTH_FACTOR.get(rule.prusa, 1.125)
        put(rule.target(flavor), f"{round(a, 3):g}")
    # Feature extruders. Prusa region extruders are >= 1 and "1" at print level normally
    # means "the object's extruder"; Bambu/Orca use 0 for that (Orca's legacy handler does
    # the same rewrite). Numbers still need remapping to filament slots by the caller.
    if flavor == "orca":
        pe = pv.scalar("perimeter_extruder")
        if pe:
            put("inner_wall_filament_id", "0" if pe == "1" else pe)
        se = pv.scalar("solid_infill_extruder")
        if se:
            put("top_surface_filament_id", "0" if se == "1" else se)
            put("bottom_surface_filament_id", "0" if se == "1" else se)
        # Organic trees read the *_organic variants in Orca.
        if (pv.scalar("support_material_style") or "") == "organic":
            for pk, ok in (("support_tree_angle", "tree_support_branch_angle_organic"),
                           ("support_tree_branch_diameter", "tree_support_branch_diameter_organic"),
                           ("support_tree_branch_distance", "tree_support_branch_distance_organic")):
                if pv.has(pk):
                    put(ok, pv.scalar(pk))
    # Max volumetric slope: Orca has one value.
    if flavor == "orca":
        pos = _num(pv.scalar("max_volumetric_extrusion_rate_slope_positive"))
        neg = _num(pv.scalar("max_volumetric_extrusion_rate_slope_negative"))
        cand = [x for x in (pos, neg) if x]
        if cand:
            put("max_volumetric_extrusion_rate_slope", f"{min(cand):g}")
    return out


def bed_temperature_values(prusa_bed: List[str], prusa_first: List[str], flavor: str = "bambu") -> dict:
    """Prusa bed temps (per filament) -> every plate type key (vectors).

    All plate types are written: Bambu's Print::validate() rejects a plate whose
    first-layer temperature for a used filament is 0 ("Plate %d: %s does not support
    filament %s"), and the user may switch plates after import."""
    keys = BED_TEMP_KEYS_ORCA if flavor == "orca" else BED_TEMP_KEYS
    out = {}
    for _plate, (k, k1) in keys.items():
        out[k] = [to_int(x) or "0" for x in prusa_bed]
        out[k1] = [to_int(x) or "0" for x in prusa_first]
    return out


def flush_matrix(cfg: Dict[str, str], n: int) -> List[str]:
    """Prusa purge volumes -> Bambu/Orca flush_volumes_matrix (row-major n*n, mm3)."""
    pv = PrusaView(cfg)
    if to_bool(pv.scalar("wiping_volumes_use_custom_matrix") or "0") == "1":
        m = prusa_split("wiping_volumes_matrix", pv.raw("wiping_volumes_matrix")) or []
        if len(m) == n * n:
            return [f"{_num(x) or 0:g}" for x in m]
    purge = _num(pv.raw("multimaterial_purging")) or 140.0
    mult = prusa_split("filament_purge_multiplier", pv.raw("filament_purge_multiplier")) or ["100%"]
    mult = (mult + [mult[-1]] * n)[:n]
    return ["0" if i == j else f"{purge * (_num(mult[j]) or 100) / 100:g}" for i in range(n) for j in range(n)]


def z_hop_types(cfg: Dict[str, str]) -> List[str]:
    """Prusa travel_ramping_lift (per extruder) -> z_hop_types ('Slope Lift' / 'Normal Lift')."""
    vals = prusa_split("travel_ramping_lift", PrusaView(cfg).raw("travel_ramping_lift")) or ["0"]
    return ["Slope Lift" if to_bool(v) == "1" else "Normal Lift" for v in vals]


# -- per-object / per-volume -----------------------------------------------------------------------

def object_rules(flavor: str = "bambu") -> List[Rule]:
    """PRINT_RULES whose target is allowed per object/part in the target (PrintObjectConfig
    + PrintRegionConfig). Prusa object metadata (Slic3r_PE_model.config) goes through these."""
    S = load_schema(flavor)
    allowed = set(S.OBJECT_KEYS) | set(S.REGION_KEYS) | {"extruder"}
    return [r for r in PRINT_RULES if r.target(flavor) in allowed]


# Prusa per-object keys handled outside PRINT_RULES.
OBJECT_SPECIAL = {
    "extruder": "extruder",                  # 1-based in all three (0 = default)
    "wipe_into_infill": "flush_into_infill",
    "wipe_into_objects": "flush_into_objects",
}


# -- self check ------------------------------------------------------------------------------------

def self_check() -> List[str]:
    """Verify every key and every emitted enum value against the generated schemas."""
    problems = []
    for flavor in ("bambu", "orca"):
        S = load_schema(flavor)
        tables = [("PRINT", PRINT_RULES), ("FILAMENT", FILAMENT_RULES), ("PRINTER", PRINTER_RULES),
                  ("EXTRUDER", EXTRUDER_RULES), ("FIL2PRINTER", FILAMENT_TO_PRINTER_RULES)]
        for tname, rules in tables:
            for r in rules:
                if r.prusa not in P.OPTIONS:
                    problems.append(f"{tname}: prusa key {r.prusa} unknown")
                t = r.target(flavor)
                if not t:
                    continue
                if t not in S.OPTIONS:
                    problems.append(f"{flavor} {tname}: target {t} (from {r.prusa}) unknown")
                    continue
                conv = r.converter(flavor)
                acc = S.ENUM_ACCEPTED.get(t) or S.OPTIONS[t][2]
                kind = S.OPTIONS[t][0]
                if conv is not None and hasattr(conv, "mapping"):
                    outs = set(conv.mapping.values()) | set(conv.passthrough)
                    if kind in ("enum", "enums") and acc:
                        bad = outs - set(acc)
                        if bad:
                            problems.append(f"{flavor}: {t} <- {r.prusa}: values {sorted(bad)} not accepted")
                    pin = set(conv.mapping) | set(conv.passthrough)
                    pacc = P.ENUM_ACCEPTED.get(r.prusa) or P.OPTIONS.get(r.prusa, (0, 0, None))[2]
                    if pacc and P.OPTIONS[r.prusa][0] in ("enum", "enums"):
                        missing = set(pacc) - pin
                        if missing:
                            problems.append(f"{flavor}: {t} <- {r.prusa}: Prusa values {sorted(missing)} unmapped")
        for pk, bk, ok in OVERHANG_SPEED_RULES:
            k = ok if flavor == "orca" else bk
            if k and k not in S.OPTIONS:
                problems.append(f"{flavor}: overhang target {k} unknown")
        for pk, tk in MACHINE_LIMIT_RULES:
            if tk not in S.OPTIONS or pk not in P.OPTIONS:
                problems.append(f"{flavor}: machine limit {pk}->{tk} unknown")
        for k, k1 in (BED_TEMP_KEYS_ORCA if flavor == "orca" else BED_TEMP_KEYS).values():
            for kk in (k, k1):
                if kk not in S.OPTIONS:
                    problems.append(f"{flavor}: bed key {kk} unknown")
        for plate in (BED_TEMP_KEYS_ORCA if flavor == "orca" else BED_TEMP_KEYS):
            if plate not in S.ENUM_ACCEPTED.get("curr_bed_type", ()):
                problems.append(f"{flavor}: plate {plate!r} not a curr_bed_type value")
        for k in ("enable_support", "support_type", "support_style", "ironing_type", "wall_sequence",
                  "xy_hole_compensation", "flush_volumes_matrix", "z_hop_types", "curr_bed_type",
                  "wipe_tower_x", "wipe_tower_y", "wipe_tower_rotation_angle", "flush_into_infill",
                  "flush_into_objects", "extruder_clearance_height_to_lid"):
            if k not in S.OPTIONS:
                problems.append(f"{flavor}: special key {k} unknown")
        for v in ("normal(auto)", "normal(manual)", "tree(auto)", "tree(manual)"):
            if v not in S.ENUM_ACCEPTED["support_type"]:
                problems.append(f"{flavor}: support_type {v}")
        for v in ("grid", "snug", "default", "organic" if flavor == "orca" else "tree_organic"):
            if v not in S.ENUM_ACCEPTED["support_style"]:
                problems.append(f"{flavor}: support_style {v}")
        for v in ("no ironing", "top", "topmost", "solid"):
            if v not in S.ENUM_ACCEPTED["ironing_type"]:
                problems.append(f"{flavor}: ironing_type {v}")
        for v in ("outer wall/inner wall", "inner wall/outer wall"):
            if v not in S.ENUM_ACCEPTED["wall_sequence"]:
                problems.append(f"{flavor}: wall_sequence {v}")
        for v in ("Slope Lift", "Normal Lift"):
            if v not in S.ENUM_ACCEPTED["z_hop_types"]:
                problems.append(f"{flavor}: z_hop_types {v}")
        if flavor == "orca":
            for v in ("everywhere", "nowhere"):
                if v not in S.ENUM_ACCEPTED["gap_fill_target"]:
                    problems.append(f"orca: gap_fill_target {v}")
    return problems


if __name__ == "__main__":
    probs = self_check()
    print("\n".join(probs) if probs else "self_check: OK")
