"""Write a Bambu Studio / OrcaSlicer .3mf project from a PrusaProject.

Bambu's layout differs from PrusaSlicer's in three ways that matter here:

* Geometry is split per part. Every volume of an object becomes its own ``<object>``
  (with its own mesh) in ``3D/Objects/object_N.model``; the object in the root model only
  holds ``<components>`` pointing at those parts (3MF production extension), and
  ``3D/_rels/3dmodel.model.rels`` lists the part files. That rels file also tells Bambu
  Studio the file is not a PrusaSlicer project, so it must always be present.
* Part metadata (names, types, extruders, per-part settings, text) and the plates live in
  ``Metadata/model_settings.config``; the slicer settings are JSON in
  ``Metadata/project_settings.config``.
* All plates share one world coordinate system, laid out in a grid of ceil(sqrt(n))
  columns, each plate 1.2 x its own size from the next, rows going towards -Y. Plate
  membership is recomputed from positions on load, so every instance has to be moved
  into its plate's rectangle.

PrusaSlicer stores the volumes of an object as triangle ranges of one merged mesh whose
vertices are already in object coordinates. Those ranges are cut out unchanged (Bambu
and Orca re-centre each part's mesh on load, which preserves the geometry), so vertices
and per-triangle paint data move over as-is. Text parts are the exception: they need
their own frame to stay editable, which is recovered from the emboss shape's fix matrix.
"""

from __future__ import annotations

import math
import re
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from xml.sax.saxutils import escape, quoteattr

from . import ns
from .paint import PaintError, transform_states
from .reader import (
    PAINT_COLOR,
    PAINT_FUZZY,
    PAINT_SEAM,
    PAINT_SUPPORTS,
    BuildItem,
    ModelObject,
    PrusaProject,
    Volume,
    bed_translation,
)
from .transform import Transform

_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\Z")
_HEX_RE = re.compile(r"[0-9A-Fa-f]+\Z")

ROOT_MODEL = "3D/3dmodel.model"
MODEL_RELS = "3D/_rels/3dmodel.model.rels"
MODEL_SETTINGS = "Metadata/model_settings.config"
PROJECT_SETTINGS = "Metadata/project_settings.config"
SLICE_INFO = "Metadata/slice_info.config"
CUSTOM_GCODE = "Metadata/custom_gcode_per_layer.xml"
LAYER_RANGES = "Metadata/layer_config_ranges.xml"
LAYER_HEIGHTS = "Metadata/layer_heights_profile.txt"

PLATE_GAP = 0.2  # LOGICAL_PART_PLATE_GAP

VOLUME_SUBTYPES = {
    "ModelPart": "normal_part",
    "NegativeVolume": "negative_part",
    "ParameterModifier": "modifier_part",
    "SupportEnforcer": "support_enforcer",
    "SupportBlocker": "support_blocker",
}

# PrusaSlicer triangle attribute -> Bambu/Orca triangle attribute.
PAINT_ATTRS = {
    PAINT_COLOR: "paint_color",
    PAINT_SUPPORTS: "paint_supports",
    PAINT_SEAM: "paint_seam",
    PAINT_FUZZY: "paint_fuzzy_skin",
}

CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
    ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
    ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
    ' <Default Extension="png" ContentType="image/png"/>\n'
    ' <Default Extension="gcode" ContentType="text/x.gcode"/>\n'
    "</Types>\n"
)

_MODEL_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    f'<model unit="millimeter" xml:lang="en-US" xmlns="{ns.CORE}" xmlns:BambuStudio="{ns.BAMBU}" '
    f'xmlns:p="{ns.PRODUCTION}" requiredextensions="p">\n'
)
IDENTITY16 = "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"

# Bambu Studio's CUR_FONT_VERSION: text_info written with it is read as a current text.
TEXT_FONT_VERSION = "2.8"


@dataclass
class WritePlan:
    """Everything about the target the writer needs, decided by convert.py."""

    slicer: str = "bambu"  # bambu | orca
    application: str = "BambuStudio-02.01.00.00"
    orca_version: str = ""  # written as <metadata name="OrcaSlicer"> for an Orca target
    project_settings: Optional[dict] = None  # the JSON config; None = no settings at all
    plate_size: tuple = (256.0, 256.0)  # printable area of the target printer
    plate_min: tuple = (0.0, 0.0)  # printable area origin
    # Prusa per-object / per-part / per-range settings -> Bambu keys (None: drop them).
    object_settings: Optional[Callable[[Dict[str, str], str], Dict[str, str]]] = None
    title: str = ""


@dataclass
class WriteStats:
    objects_written: int = 0
    parts_written: int = 0
    build_items_written: int = 0
    painted_triangles: int = 0
    plates_written: int = 1
    filament_map: Optional[dict] = None  # Prusa extruder -> filament slot, when renumbered
    settings_written: bool = False
    filaments: int = 0
    printer_name: str = ""
    print_name: str = ""
    filament_names: List[str] = field(default_factory=list)
    custom_gcodes_written: int = 0
    layer_ranges_written: int = 0
    text_parts: int = 0
    warnings: List[str] = field(default_factory=list)


# -- small helpers ------------------------------------------------------------------------

def plate_columns(n: int) -> int:
    """PartPlateList::compute_colum_count: ceil(sqrt(n))."""
    n = max(1, n)
    c = math.isqrt(n)
    return c if c * c == n else c + 1


def plate_origin(index: int, count: int, size: tuple) -> tuple:
    cols = plate_columns(count)
    row, col = divmod(index, cols)
    return (col * size[0] * (1.0 + PLATE_GAP), -row * size[1] * (1.0 + PLATE_GAP))


def _num(v: float) -> str:
    if v == 0:
        return "0"
    s = f"{v:.9g}"
    return s


def _attrs(d: Dict[str, str]) -> str:
    return " ".join(f"{k}={quoteattr(str(v))}" for k, v in d.items())


def _meta(key: str, value, indent: str = "    ") -> str:
    return f"{indent}<metadata key={quoteattr(key)} value={quoteattr(str(value))}/>\n"


def to_matrix16(t: Transform) -> str:
    m = t.m
    rows = ((m[0], m[3], m[6], m[9]), (m[1], m[4], m[7], m[10]), (m[2], m[5], m[8], m[11]), (0, 0, 0, 1))
    return " ".join(_num(v) for r in rows for v in r)


def remap_paint(code: str, mapping: Dict[int, int], cache: dict) -> str:
    """Rewrite paint states through ``mapping`` (0 = the part's own filament stays 0)."""
    hit = cache.get(code)
    if hit is None:
        try:
            hit = transform_states(code, mapping) if mapping else code.upper()
        except PaintError:
            hit = code.upper()
        cache[code] = hit
    return hit


# -- geometry ------------------------------------------------------------------------------

@dataclass
class _Part:
    volume: Volume
    xml: str  # the <object> element for the part file
    face_count: int
    painted: int
    transform: Transform  # component transform (part -> object)
    extruder: Optional[int]  # Bambu filament slot, None = the object's


def _volume_frame(project: PrusaProject, mo: ModelObject, vol: Volume, verts: list) -> Optional[Transform]:
    """The part -> object transform of a text volume: V = T(center(B)) * fix^-1, with B
    the baked mesh and fix the emboss shape's fix matrix (3mf.cpp create_fix)."""
    if not vol.shape or not vol.shape.get("transform"):
        return None
    try:
        fix = Transform.parse(vol.shape["transform"])
        fix_inv = fix.inverse()
    except ValueError:
        return None
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    if not xs:
        return None
    c = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
    return fix_inv.compose(Transform.translation(*c))


def _split_object(project: PrusaProject, mo: ModelObject, slot_of: Callable[[Optional[int]], Optional[int]],
                  paint_map: Dict[int, int], paint_cache: dict, text_mode: str, warnings: list) -> List[_Part]:
    mesh = mo.mesh
    vert_el = mesh.find(ns.q(ns.CORE, "vertices"))
    tri_el = mesh.find(ns.q(ns.CORE, "triangles"))
    vertices = list(vert_el) if vert_el is not None else []
    triangles = list(tri_el) if tri_el is not None else []
    parts: List[_Part] = []
    v_tag, t_tag = ns.q(ns.CORE, "vertex"), ns.q(ns.CORE, "triangle")
    for vol in mo.volumes:
        tris = [t for t in triangles[vol.firstid:vol.lastid + 1] if t.tag == t_tag]
        if not tris:
            continue
        try:
            idx = [(int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))) for t in tris]
        except (TypeError, ValueError):
            warnings.append(f"object {mo.name!r}: part {vol.name!r} has malformed triangles; skipped")
            continue
        lo = min(min(i) for i in idx)
        hi = max(max(i) for i in idx)
        if lo < 0 or hi >= len(vertices):
            warnings.append(f"object {mo.name!r}: part {vol.name!r} references missing vertices; skipped")
            continue
        vsrc = [v for v in vertices[lo:hi + 1]]
        transform = Transform.identity()
        local_pts = None
        if vol.text is not None:
            pts = []
            for v in vsrc:
                if v.tag == v_tag:
                    pts.append((float(v.get("x")), float(v.get("y")), float(v.get("z"))))
            frame = _volume_frame(project, mo, vol, pts)
            if frame is not None:
                try:
                    inv = frame.inverse()
                    local_pts = [inv.apply_point(*p) for p in pts]
                    transform = frame
                except ValueError:
                    local_pts = None
        lines = ["   <mesh>\n    <vertices>\n"]
        bad = False
        if local_pts is not None:
            for (x, y, z) in local_pts:
                lines.append(f'     <vertex x="{_num(x)}" y="{_num(y)}" z="{_num(z)}"/>\n')
        else:
            for v in vsrc:
                xyz = [v.get(a) or "" for a in ("x", "y", "z")]
                if not all(_NUM_RE.match(c.strip()) for c in xyz):
                    bad = True
                    break
                lines.append(f'     <vertex x="{xyz[0].strip()}" y="{xyz[1].strip()}" z="{xyz[2].strip()}"/>\n')
        if bad:
            warnings.append(f"object {mo.name!r}: part {vol.name!r} has malformed vertices; skipped")
            continue
        lines.append("    </vertices>\n    <triangles>\n")
        painted = bad_paint = 0
        for t, (a, b, c) in zip(tris, idx):
            extra = []
            for src, dst in PAINT_ATTRS.items():
                val = (t.get(src) or "").strip()
                if val and not _HEX_RE.match(val):
                    bad_paint += 1
                    continue
                if val:
                    if src == PAINT_COLOR:
                        val = remap_paint(val, paint_map, paint_cache)
                        painted += 1
                    else:
                        val = val.upper()
                    extra.append(f' {dst}="{val}"')
            lines.append(f'     <triangle v1="{a - lo}" v2="{b - lo}" v3="{c - lo}"{"".join(extra)}/>\n')
        lines.append("    </triangles>\n   </mesh>\n")
        if bad_paint:
            warnings.append(f"object {mo.name!r}: {bad_paint} malformed paint value(s) dropped")
        parts.append(_Part(vol, "".join(lines), len(tris), painted, transform, slot_of(vol.extruder)))
    return parts


# -- text ----------------------------------------------------------------------------------

def _f(v, default: float) -> float:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def bambu_text_info(text: Dict[str, str], shape: Optional[Dict[str, str]]) -> Dict[str, str]:
    """PrusaSlicer slic3rpe:text / slic3rpe:shape -> Bambu Studio <text_info> attributes."""
    shape = shape or {}
    face = (text.get("face_name") or text.get("family") or text.get("style_name") or "").strip()
    weight = (text.get("weight") or "").lower()
    style = (text.get("style") or "").lower()
    boldness = _f(text.get("boldness"), 0.0)
    skew = _f(text.get("skew"), 0.0)
    if text.get("per_glyph") == "1":
        surface_type = "3"
    elif shape.get("use_surface") == "1":
        surface_type = "1"
    else:
        surface_type = "0"
    horizontal = text.get("horizontal") if text.get("horizontal") in ("left", "center", "right") else "center"
    vertical = text.get("vertical") if text.get("vertical") in ("top", "middle", "bottom") else "middle"
    return {
        "text": text.get("text", ""),
        "font_name": face,
        "font_version": TEXT_FONT_VERSION,
        "style_name": text.get("style_name", ""),
        "boldness": f"{boldness:g}",
        "skew": f"{skew:g}",
        "font_index": "0",
        "font_size": f"{max(_f(text.get('line_height'), 10.0), 0.1):g}",
        "thickness": f"{max(_f(shape.get('depth'), 2.0), 0.01):g}",
        "embeded_depth": "0",
        "rotate_angle": "0",
        "text_gap": f"{_f(text.get('char_gap'), 0.0):g}",
        "line_gap": f"{_f(text.get('line_gap'), 0.0):g}",
        "horizontal": horizontal,
        "vertical": vertical,
        "bold": "1" if weight == "bold" or boldness > 0 else "0",
        "italic": "1" if style == "italic" or skew != 0 else "0",
        "surface_type": surface_type,
        "hit_mesh": "-1",
        "hit_position": "0 0 0",
        "hit_normal": "0 0 1",
    }


def _text_xml(part: _Part, slicer: str, local_center: Optional[tuple]) -> str:
    vol = part.volume
    if vol.text is None:
        return ""
    if slicer == "orca":
        shape = dict(vol.shape or {})
        c = local_center or (0.0, 0.0, 0.0)
        shape["transform"] = Transform.translation(*c).to_string()
        shape.setdefault("scale", "0.001")
        return (f"      <slic3rpe:shape {_attrs(shape)}/>\n"
                f"      <slic3rpe:text {_attrs(vol.text)}/>\n")
    return f"      <text_info {_attrs(bambu_text_info(vol.text, vol.shape))}/>\n"


def _local_center(xml: str) -> Optional[tuple]:
    """Bounding-box centre of a part mesh written by _split_object (text parts only)."""
    xs, ys, zs = [], [], []
    for line in xml.splitlines():
        line = line.strip()
        if line.startswith("<vertex "):
            try:
                parts = dict(p.split("=") for p in line[8:-2].split())
                xs.append(float(parts["x"].strip('"')))
                ys.append(float(parts["y"].strip('"')))
                zs.append(float(parts["z"].strip('"')))
            except (ValueError, KeyError):
                continue
    if not xs:
        return None
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)


# -- the writer ------------------------------------------------------------------------------

def _item_bbox(project: PrusaProject, item: BuildItem) -> Optional[tuple]:
    mo = project.objects[item.object_index]
    verts = mo.mesh.find(ns.q(ns.CORE, "vertices"))
    if verts is None:
        return None
    t = item.transform
    xs, ys = [], []
    for v in verts:
        try:
            x, y, _z = t.apply_point(float(v.get("x")), float(v.get("y")), float(v.get("z")))
        except (TypeError, ValueError):
            continue
        xs.append(x)
        ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _shift(t: Transform, dx: float, dy: float) -> Transform:
    m = list(t.m)
    m[9] += dx
    m[10] += dy
    return Transform(tuple(m))


def write_bambu_3mf(
    project: PrusaProject,
    out_path: str,
    plan: WritePlan,
    beds: Optional[List[int]] = None,
    filament_map: Optional[Dict[int, int]] = None,
) -> WriteStats:
    """Write ``project`` (restricted to ``beds``) as a Bambu Studio / OrcaSlicer project.

    ``filament_map`` maps every PrusaSlicer extruder to a Bambu filament slot (1-based);
    missing extruders keep their number.
    """
    warnings: List[str] = []
    fmap = {int(k): int(v) for k, v in (filament_map or {}).items()}

    def slot_of(ext: Optional[int]) -> Optional[int]:
        if not ext:
            return None
        return fmap.get(ext, ext)

    paint_map = {k: v for k, v in fmap.items() if k != v}
    paint_cache: dict = {}

    wanted = sorted(set(beds)) if beds else project.bed_ids
    items = [bi for bi in project.build_items if bi.bed in wanted]
    plates = sorted({bi.bed for bi in items}) or [wanted[0] if wanted else 1]
    plate_of_bed = {b: i for i, b in enumerate(plates)}
    n_plates = len(plates)

    # Placement: keep each bed's layout, centred on its plate.
    pw, pd = plan.plate_size
    plate_center = (plan.plate_min[0] + pw / 2.0, plan.plate_min[1] + pd / 2.0)
    bw, bd = project.bed_size
    bed_center = (project.bed_origin[0] + bw / 2.0, project.bed_origin[1] + bd / 2.0)
    legacy = project.legacy_beds
    shift_of_bed: Dict[int, tuple] = {}
    for b in plates:
        tx, ty = bed_translation(b - 1, project.bed_size, legacy)
        ox, oy = plate_origin(plate_of_bed[b], n_plates, plan.plate_size)
        shift_of_bed[b] = (ox + plate_center[0] - (tx + bed_center[0]), oy + plate_center[1] - (ty + bed_center[1]))
    off_plate = 0
    for bi in items:
        bb = _item_bbox(project, bi)
        if bb is None:
            continue
        dx, dy = shift_of_bed[bi.bed]
        tx, ty = bed_translation(bi.bed - 1, project.bed_size, legacy)
        lx0, ly0 = bb[0] - tx - bed_center[0] + plate_center[0], bb[1] - ty - bed_center[1] + plate_center[1]
        lx1, ly1 = bb[2] - tx - bed_center[0] + plate_center[0], bb[3] - ty - bed_center[1] + plate_center[1]
        if (lx0 < plan.plate_min[0] - 0.5 or ly0 < plan.plate_min[1] - 0.5
                or lx1 > plan.plate_min[0] + pw + 0.5 or ly1 > plan.plate_min[1] + pd + 0.5):
            off_plate += 1
    if off_plate:
        warnings.append(
            f"{off_plate} object(s) do not fit on the {pw:g} x {pd:g} mm plate of the target printer "
            "(the PrusaSlicer bed is larger); arrange the plate after opening"
        )

    # Objects in output order (first seen among the selected items).
    obj_order: List[int] = []
    for bi in items:
        if bi.object_index not in obj_order:
            obj_order.append(bi.object_index)

    stats = WriteStats(plates_written=n_plates, filament_map={k: v for k, v in fmap.items() if k != v} or None)
    root_objects: List[str] = []
    build_lines: List[str] = []
    rels: List[str] = []
    part_files: Dict[str, str] = {}
    settings_xml: List[str] = ['<?xml version="1.0" encoding="UTF-8"?>\n<config>\n']
    plate_instances: Dict[int, List[tuple]] = {p: [] for p in range(n_plates)}
    out_index: Dict[int, int] = {}  # Prusa object index (1-based) -> output object index (1-based)
    next_id = 1
    identify = 100
    translate = plan.object_settings

    seq = 0
    for oi in obj_order:
        mo = project.objects[oi]
        parts = _split_object(project, mo, slot_of, paint_map, paint_cache, plan.slicer, warnings)
        if not parts:
            warnings.append(f"object {mo.name!r} has no geometry; skipped")
            continue
        seq += 1  # numbers only the objects written: layer ranges / heights refer to these
        out_index[mo.index] = seq
        obj_slot = slot_of(mo.extruder) or slot_of(1) or 1
        if len(parts) == 1 and parts[0].extruder:
            # Bambu / Orca drop the extruder of a single-part object's part: the object carries it.
            obj_slot = parts[0].extruder
        part_ids = list(range(next_id, next_id + len(parts)))
        obj_id = next_id + len(parts)
        next_id = obj_id + 1
        file_name = f"3D/Objects/object_{obj_id}.model"
        body = [_MODEL_HEADER, ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n <resources>\n']
        for k, (pid, part) in enumerate(zip(part_ids, parts)):
            uuid = f"{(k + (seq << 16)):08x}-81cb-4c03-9d28-80fed5dfa1dc"
            body.append(f'  <object id="{pid}" p:UUID="{uuid}" type="model">\n{part.xml}  </object>\n')
        body.append(" </resources>\n <build/>\n</model>\n")
        part_files[file_name] = "".join(body)
        rels.append(file_name)

        comps = []
        for k, (pid, part) in enumerate(zip(part_ids, parts)):
            cuuid = f"{seq:04x}{k:04x}-b206-40ff-9872-83e8017abed1"
            comps.append(f'    <component p:path="/{file_name}" objectid="{pid}" p:UUID="{cuuid}" '
                         f'transform="{part.transform.to_string()}"/>\n')
        root_objects.append(
            f'  <object id="{obj_id}" p:UUID="{seq:08x}-61cb-4c03-9d28-80fed5dfa1dc" type="model">\n'
            f'   <components>\n{"".join(comps)}   </components>\n  </object>\n'
        )

        # model_settings.config
        settings_xml.append(f'  <object id="{obj_id}">\n')
        settings_xml.append(_meta("name", mo.name or f"Object {seq}"))
        settings_xml.append(_meta("extruder", obj_slot))
        if translate and mo.settings:
            for k, v in sorted(translate(mo.settings, f"object {mo.name}").items()):
                if k not in ("extruder", "name"):
                    settings_xml.append(_meta(k, v))
        single = len(parts) == 1
        for pid, part in zip(part_ids, parts):
            vol = part.volume
            subtype = VOLUME_SUBTYPES.get(vol.volume_type, "normal_part")
            settings_xml.append(f'    <part id="{pid}" subtype="{subtype}">\n')
            settings_xml.append(_meta("name", vol.name or mo.name or f"Part {pid}", "      "))
            settings_xml.append(_meta("matrix", to_matrix16(part.transform) if vol.text is not None else IDENTITY16,
                                      "      "))
            for k, v in vol.source.items():
                settings_xml.append(_meta(k, v, "      "))
            if part.extruder and not single and subtype in ("normal_part", "modifier_part", "negative_part"):
                settings_xml.append(_meta("extruder", part.extruder, "      "))
            if translate and vol.settings:
                for k, v in sorted(translate(vol.settings, f"part {vol.name}").items()):
                    if k not in ("extruder", "name"):
                        settings_xml.append(_meta(k, v, "      "))
            if vol.text is not None:
                settings_xml.append(_text_xml(part, plan.slicer, _local_center(part.xml)))
                stats.text_parts += 1
            settings_xml.append(
                f'      <mesh_stat face_count="{part.face_count}" edges_fixed="0" degenerate_facets="0" '
                'facets_removed="0" facets_reversed="0" backwards_edges="0"/>\n'
            )
            settings_xml.append("    </part>\n")
            stats.painted_triangles += part.painted
        settings_xml.append("  </object>\n")
        stats.objects_written += 1
        stats.parts_written += len(parts)

        inst = 0
        for bi in items:
            if bi.object_index != oi:
                continue
            dx, dy = shift_of_bed[bi.bed]
            t = _shift(bi.transform, dx, dy)
            build_lines.append(
                f'  <item objectid="{obj_id}" p:UUID="{(stats.build_items_written + 1):08x}-b1ec-4553-aec9-835e5b724bb4" '
                f'transform="{t.to_string()}" printable="{1 if bi.printable else 0}"/>\n'
            )
            stats.build_items_written += 1
            p = plate_of_bed[bi.bed]
            if not any(o == obj_id for o, _i, _id in plate_instances[p]):
                identify += 1
                plate_instances[p].append((obj_id, inst, identify))
            inst += 1

    for p in range(n_plates):
        settings_xml.append("  <plate>\n")
        settings_xml.append(_meta("plater_id", p + 1))
        settings_xml.append(_meta("plater_name", ""))
        settings_xml.append(_meta("locked", "false"))
        for obj_id, inst, ident in plate_instances[p]:
            settings_xml.append("    <model_instance>\n")
            settings_xml.append(_meta("object_id", obj_id, "      "))
            settings_xml.append(_meta("instance_id", inst, "      "))
            settings_xml.append(_meta("identify_id", ident, "      "))
            settings_xml.append("    </model_instance>\n")
        settings_xml.append("  </plate>\n")
    settings_xml.append("</config>\n")

    # Root model.
    root = [_MODEL_HEADER]
    root.append(f' <metadata name="Application">{escape(plan.application)}</metadata>\n')
    root.append(' <metadata name="BambuStudio:3mfVersion">1</metadata>\n')
    if plan.slicer == "orca" and plan.orca_version:
        root.append(f' <metadata name="OrcaSlicer">{escape(plan.orca_version)}</metadata>\n')
    title = plan.title or project.title
    if title:
        root.append(f" <metadata name=\"Title\">{escape(title)}</metadata>\n")
    root.append(" <resources>\n")
    root.extend(root_objects)
    root.append(" </resources>\n")
    root.append(' <build p:UUID="2c7c17d8-22b5-4d84-8835-1976022ea369">\n')
    root.extend(build_lines)
    root.append(" </build>\n</model>\n")

    # Side files.
    custom_xml, n_codes = _custom_gcodes(project, plates, fmap, plan, warnings)
    ranges_xml, n_ranges = _layer_ranges(project, out_index, plan, warnings)
    heights_txt = _layer_heights(project, out_index)
    stats.custom_gcodes_written = n_codes
    stats.layer_ranges_written = n_ranges

    settings_json = None
    if plan.project_settings is not None:
        cfg = dict(plan.project_settings)
        _apply_wipe_tower(cfg, project, plates, plan, shift_of_bed)
        settings_json = _json(cfg)
        stats.settings_written = True
        stats.printer_name = str(cfg.get("printer_settings_id", ""))
        stats.print_name = str(cfg.get("print_settings_id", ""))
        ids = cfg.get("filament_settings_id")
        stats.filament_names = list(ids) if isinstance(ids, list) else ([ids] if ids else [])
        stats.filaments = len(cfg.get("filament_colour") or [])

    thumb = project.thumbnail
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        if thumb:
            z.writestr(zipfile.ZipInfo("Metadata/plate_1.png", date_time=(2026, 1, 1, 0, 0, 0)), thumb,
                       compress_type=zipfile.ZIP_STORED)
        z.writestr(ROOT_MODEL, "".join(root))
        z.writestr(MODEL_RELS, _rels_xml([f"/{r}" for r in rels]))
        for name, data in part_files.items():
            z.writestr(name, data)
        if heights_txt:
            z.writestr(LAYER_HEIGHTS, heights_txt)
        if ranges_xml:
            z.writestr(LAYER_RANGES, ranges_xml)
        if custom_xml:
            z.writestr(CUSTOM_GCODE, custom_xml)
        if settings_json is not None:
            z.writestr(PROJECT_SETTINGS, settings_json)
        z.writestr(MODEL_SETTINGS, "".join(settings_xml))
        z.writestr(SLICE_INFO, _slice_info(plan))
        z.writestr("_rels/.rels", _root_rels(bool(thumb)))

    stats.warnings = warnings
    return stats


def _json(cfg: dict) -> str:
    import json

    def norm(v):
        if isinstance(v, list):
            return [str(x) for x in v]
        return str(v)

    return json.dumps({k: norm(v) for k, v in cfg.items()}, indent=4, ensure_ascii=False) + "\n"


def _rels_xml(targets: List[str]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n', f'<Relationships xmlns="{ns.RELS}">\n']
    for i, t in enumerate(targets, start=1):
        out.append(f' <Relationship Target={quoteattr(t)} Id="rel-{i}" Type="{ns.REL_TYPE_STARTPART}"/>\n')
    out.append("</Relationships>\n")
    return "".join(out)


def _root_rels(with_thumbnail: bool) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n', f'<Relationships xmlns="{ns.RELS}">\n',
           f' <Relationship Target="/{ROOT_MODEL}" Id="rel-1" Type="{ns.REL_TYPE_STARTPART}"/>\n']
    if with_thumbnail:
        out.append(f' <Relationship Target="/Metadata/plate_1.png" Id="rel-2" Type="{ns.REL_TYPE_THUMBNAIL}"/>\n')
        out.append(' <Relationship Target="/Metadata/plate_1.png" Id="rel-4" '
                   'Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-middle"/>\n')
    out.append("</Relationships>\n")
    return "".join(out)


def _slice_info(plan: WritePlan) -> str:
    ver = plan.application.split("-", 1)[-1]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <header>\n'
            '    <header_item key="X-BBL-Client-Type" value="slicer"/>\n'
            f'    <header_item key="X-BBL-Client-Version" value={quoteattr(ver)}/>\n'
            "  </header>\n</config>\n")


def _custom_gcodes(project: PrusaProject, plates: List[int], fmap: Dict[int, int], plan: WritePlan, warnings):
    if not any(project.custom_gcodes.get(b) and project.custom_gcodes[b].items for b in plates):
        return None, 0
    n_slots = len((plan.project_settings or {}).get("filament_colour") or []) or max([1] + list(fmap.values()))
    lines = ['<?xml version="1.0" encoding="utf-8"?>\n<custom_gcodes_per_layer>\n']
    count = 0
    for p, bed in enumerate(plates):
        bg = project.custom_gcodes.get(bed)
        if not bg or not bg.items:
            continue
        lines.append(f'<plate>\n<plate_info id="{p + 1}"/>\n')
        for it in bg.items:
            ext = fmap.get(it.extruder) if fmap else it.extruder
            if ext is None:
                if it.type in (0, 2):  # a colour / tool change of an extruder nothing prints with
                    w = (f"bed {bed}: {'colour' if it.type == 0 else 'tool'} change at Z {it.print_z:g} "
                         f"for unused extruder {it.extruder} skipped")
                    if w not in warnings:
                        warnings.append(w)
                    continue
                ext = 1
            gcode = {0: "M600", 1: "M601", 2: "tool_change", 3: ""}.get(it.type, it.extra)
            lines.append(
                f'<layer top_z="{it.print_z:g}" type="{it.type}" extruder="{ext}" color={quoteattr(it.color)} '
                f'extra={quoteattr(it.extra)} gcode={quoteattr(gcode)}/>\n'
            )
            count += 1
        mode = "SingleExtruder" if n_slots <= 1 else "MultiAsSingle"
        lines.append(f'<mode value="{mode}"/>\n</plate>\n')
    lines.append("</custom_gcodes_per_layer>\n")
    return "".join(lines), count


def _layer_ranges(project: PrusaProject, out_index: Dict[int, int], plan: WritePlan, warnings):
    if not project.layer_ranges or plan.object_settings is None:
        return None, 0
    lines = ['<?xml version="1.0" encoding="utf-8"?>\n<objects>\n']
    count = 0
    for idx, ranges in sorted(project.layer_ranges.items()):
        out = out_index.get(idx)
        if out is None:
            continue
        lines.append(f' <object id="{out}">\n')
        for rng in ranges:
            settings = plan.object_settings(rng.settings, f"layer range {rng.min_z:g}-{rng.max_z:g}")
            lines.append(f'  <range min_z="{rng.min_z:g}" max_z="{rng.max_z:g}">\n')
            for k, v in sorted(settings.items()):
                lines.append(f'   <option opt_key={quoteattr(k)}>{escape(str(v))}</option>\n')
            lines.append("  </range>\n")
            count += 1
        lines.append(" </object>\n")
    lines.append("</objects>\n")
    return ("".join(lines), count) if count else (None, 0)


def _layer_heights(project: PrusaProject, out_index: Dict[int, int]) -> Optional[str]:
    lines = []
    for idx, profile in sorted(project.layer_heights.items()):
        out = out_index.get(idx)
        if out is None:
            continue
        values = [v for v in profile.split(";") if v.strip()]
        if len(values) <= 4 or len(values) % 2:
            continue
        lines.append(f"object_id={out}|" + ";".join(values))
    return ("\n".join(lines) + "\n") if lines else None


def _apply_wipe_tower(cfg: dict, project: PrusaProject, plates: List[int], plan: WritePlan,
                      shift_of_bed: Dict[int, tuple]) -> None:
    """Per-plate prime tower positions (plate-local), from PrusaSlicer's per-bed ones."""
    pw, pd = plan.plate_size
    bw, bd = project.bed_size
    xs, ys = [], []
    try:
        default = (float(project.config.get("wipe_tower_x") or 180), float(project.config.get("wipe_tower_y") or 140))
    except ValueError:
        default = (180.0, 140.0)
    for bed in plates:
        x, y = project.wipe_towers.get(bed, (default[0], default[1], 0.0))[:2]
        # bed-local -> plate-local, keeping the position relative to the bed centre
        x = x - (project.bed_origin[0] + bw / 2.0) + (plan.plate_min[0] + pw / 2.0) - plan.plate_min[0]
        y = y - (project.bed_origin[1] + bd / 2.0) + (plan.plate_min[1] + pd / 2.0) - plan.plate_min[1]
        x = min(max(x, 0.0), max(pw - 40.0, 0.0))
        y = min(max(y, 0.0), max(pd - 40.0, 0.0))
        xs.append(f"{x:g}")
        ys.append(f"{y:g}")
    cfg["wipe_tower_x"] = xs
    cfg["wipe_tower_y"] = ys
