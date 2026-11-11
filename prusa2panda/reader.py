"""Read a PrusaSlicer .3mf project into an in-memory model.

PrusaSlicer writes one ``<object>`` per model object holding a single merged mesh: every
volume (part, modifier, negative volume, support enforcer/blocker, text) is a contiguous
triangle range of it, recorded as ``<volume firstid=.. lastid=..>`` in
``Metadata/Slic3r_PE_model.config``. The vertices are stored transformed by the volume
matrix (part -> object), and the ``matrix`` volume metadata lets a reader restore the
part's own frame. Further instances of an object are extra ``<object>`` elements with a
single ``<component>`` pointing at the first one, each with its own build item.

The reader keeps the lxml mesh elements so the writer can move vertex and triangle data
(including per-triangle paint bitstreams) without re-parsing, and extracts the bits needed
for bed selection, settings translation and multi-material bookkeeping.
"""

from __future__ import annotations

import math
import posixpath
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import lxml.etree as ET

from . import ns
from .prusa_config import PrusaConfig
from .transform import Transform

ROOT_MODEL = "3D/3dmodel.model"
PRINT_CONFIG = "Metadata/Slic3r_PE.config"
MODEL_CONFIG = "Metadata/Slic3r_PE_model.config"
CUSTOM_GCODE = "Metadata/Prusa_Slicer_custom_gcode_per_print_z.xml"
LAYER_CONFIG_RANGES = "Metadata/Prusa_Slicer_layer_config_ranges.xml"
LAYER_HEIGHTS_PROFILE = "Metadata/Slic3r_PE_layer_heights_profile.txt"
WIPE_TOWER_INFO = "Metadata/Prusa_Slicer_wipe_tower_information.xml"
CUT_INFORMATION = "Metadata/Prusa_Slicer_cut_information.xml"
FULL_SPECTRUM = "Metadata/Prusa_Slicer_full_spectrum.json"
THUMBNAIL = "Metadata/thumbnail.png"

# PrusaSlicer per-triangle paint attributes.
PAINT_COLOR = ns.q(ns.SLIC3RPE, "mmu_segmentation")
PAINT_SUPPORTS = ns.q(ns.SLIC3RPE, "custom_supports")
PAINT_SEAM = ns.q(ns.SLIC3RPE, "custom_seam")
PAINT_FUZZY = ns.q(ns.SLIC3RPE, "fuzzy_skin")

# Volume metadata keys that describe the model rather than print settings.
_VOLUME_META_KEYS = {
    "name", "modifier", "volume_type", "matrix", "extruder",
    "source_file", "source_object_id", "source_volume_id", "source_offset_x",
    "source_offset_y", "source_offset_z", "source_in_inches", "source_in_meters",
    "source_is_builtin_volume",
}
_OBJECT_META_KEYS = {"name", "extruder"}

# PrusaSlicer keeps at most this many beds (MAX_NUMBER_OF_BEDS in 2.9).
MAX_BEDS = 9


@dataclass
class Volume:
    """One PrusaSlicer volume: a triangle range of its object's merged mesh."""

    firstid: int
    lastid: int
    name: str = ""
    volume_type: str = "ModelPart"  # ModelPart | NegativeVolume | ParameterModifier | SupportBlocker | SupportEnforcer
    extruder: Optional[int] = None  # None/0 = the object's extruder
    matrix: Transform = field(default_factory=Transform.identity)  # part -> object (3MF row form)
    has_matrix: bool = False
    settings: Dict[str, str] = field(default_factory=dict)  # per-volume print settings
    text: Optional[Dict[str, str]] = None  # slic3rpe:text attributes
    shape: Optional[Dict[str, str]] = None  # slic3rpe:shape attributes
    source: Dict[str, str] = field(default_factory=dict)  # source_* metadata


@dataclass
class ModelObject:
    """A PrusaSlicer model object (one merged mesh) with its volumes."""

    id: str  # object id in 3D/3dmodel.model
    index: int  # 1-based index among model objects (keys layer ranges / heights)
    mesh: object  # lxml <mesh> element
    name: str = ""
    extruder: Optional[int] = None
    volumes: List[Volume] = field(default_factory=list)
    settings: Dict[str, str] = field(default_factory=dict)  # per-object print settings
    triangle_count: int = 0


@dataclass
class BuildItem:
    """One instance of a model object on the build plate."""

    object_index: int  # index into PrusaProject.objects
    transform: Transform
    printable: bool = True
    bed: int = 1  # 1-based PrusaSlicer bed


@dataclass
class CustomGCode:
    """One per-layer entry: colour change, pause, tool change, template or custom G-code."""

    print_z: float
    type: int  # 0 ColorChange, 1 PausePrint, 2 ToolChange, 3 Template, 4 Custom
    extruder: int
    color: str = ""
    extra: str = ""


@dataclass
class BedGCodes:
    mode: str = "SingleExtruder"  # SingleExtruder | MultiAsSingle | MultiExtruder
    items: List[CustomGCode] = field(default_factory=list)


@dataclass
class LayerRange:
    min_z: float
    max_z: float
    settings: Dict[str, str] = field(default_factory=dict)


@dataclass
class PrusaProject:
    source_path: str
    producer: str
    objects: List[ModelObject]
    build_items: List[BuildItem]
    config: PrusaConfig
    custom_gcodes: Dict[int, BedGCodes] = field(default_factory=dict)  # 1-based bed -> codes
    wipe_towers: Dict[int, tuple] = field(default_factory=dict)  # 1-based bed -> (x, y, rotation)
    layer_ranges: Dict[int, List[LayerRange]] = field(default_factory=dict)  # object index -> ranges
    layer_heights: Dict[int, str] = field(default_factory=dict)  # object index -> "z;h;z;h..."
    thumbnail: Optional[bytes] = None
    title: str = ""
    has_cut_info: bool = False
    has_full_spectrum: bool = False
    bed_size: tuple = (250.0, 210.0)
    bed_origin: tuple = (0.0, 0.0)

    @property
    def bed_ids(self) -> List[int]:
        return sorted({bi.bed for bi in self.build_items}) or [1]

    def bed_of_item(self, item: BuildItem) -> int:
        return item.bed

    @property
    def painted_triangles(self) -> int:
        n = 0
        for obj in self.objects:
            for tri in obj.mesh.iter(ns.q(ns.CORE, "triangle")):
                if tri.get(PAINT_COLOR):
                    n += 1
        return n

    @property
    def extruder_count(self) -> int:
        return max(1, len(self.config.vector("nozzle_diameter") or []))

    def extruder_colour(self, extruder: int) -> str:
        """The colour PrusaSlicer shows for an extruder: extruder_colour, else filament_colour."""
        for key in ("extruder_colour", "filament_colour"):
            vals = self.config.vector(key) or []
            if 0 < extruder <= len(vals):
                c = vals[extruder - 1].strip()
                if c.startswith("#") and len(c) >= 7:
                    return c[:7].upper()
        return ""

    def filament_type(self, extruder: int) -> str:
        vals = self.config.vector("filament_type") or []
        return vals[extruder - 1] if 0 < extruder <= len(vals) else ""


# -- helpers ----------------------------------------------------------------------------

def _parse(data: bytes):
    parser = ET.XMLParser(recover=True, huge_tree=True)
    return ET.fromstring(data, parser=parser)


def _parse_fragments(data: bytes):
    """PrusaSlicer writes some metadata files as several top-level elements (a boost
    property tree with more than one root); wrap them so they parse as one document."""
    text = data.decode("utf-8", "replace")
    if text.lstrip().startswith("<?xml"):
        text = text[text.index("?>") + 2:]
    return _parse(("<root>" + text + "</root>").encode("utf-8"))


def _normalize_path(target: str, base: str = ROOT_MODEL) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), target))


def matrix16_to_transform(text: str) -> Optional[Transform]:
    """PrusaSlicer's 16-number row-major 4x4 matrix (column vectors, translation in the
    last column) -> the 12-number 3MF row form."""
    try:
        v = [float(x) for x in text.replace(",", " ").split()]
    except ValueError:
        return None
    if len(v) != 16:
        return None
    return Transform((v[0], v[4], v[8], v[1], v[5], v[9], v[2], v[6], v[10], v[3], v[7], v[11]))


def _int_or_none(v: Optional[str]) -> Optional[int]:
    try:
        return int(float(v)) if v not in (None, "") else None
    except ValueError:
        return None


# -- reading ------------------------------------------------------------------------------

def read_prusa_3mf(path: str) -> PrusaProject:
    with zipfile.ZipFile(path, "r") as z:
        names = {n.replace("\\", "/"): n for n in z.namelist()}

        def read(name: str) -> Optional[bytes]:
            real = names.get(name)
            if real is None:  # case-insensitive fallback
                low = name.lower()
                real = next((v for k, v in names.items() if k.lower() == low), None)
            return z.read(real) if real is not None else None

        model_data = read(ROOT_MODEL)
        if model_data is None:
            raise ValueError(f"{path!r} does not contain {ROOT_MODEL}; not a 3mf project.")
        root = _parse(model_data)

        producer, title = "", ""
        for md in root.findall(ns.q(ns.CORE, "metadata")):
            if md.get("name") == "Application" and md.text:
                producer = md.text.strip()
            elif md.get("name") == "Title" and md.text:
                title = md.text.strip()

        cfg_data = read(PRINT_CONFIG)
        config = (
            PrusaConfig.from_slic3r_pe_text(cfg_data.decode("utf-8", "replace")) if cfg_data else PrusaConfig()
        )

        # Every <object> in the root model and any referenced model parts.
        trees = {ROOT_MODEL: root}
        for comp in root.iter(ns.q(ns.CORE, "component")):
            p = comp.get(ns.q(ns.PRODUCTION, "path"))
            if p:
                np_ = _normalize_path(p)
                if np_ not in trees:
                    data = read(np_)
                    if data is not None:
                        trees[np_] = _parse(data)
        elements: Dict[tuple, object] = {}
        for tpath, troot in trees.items():
            for obj in troot.iter(ns.q(ns.CORE, "object")):
                if obj.get("id") is not None:
                    elements[(tpath, obj.get("id"))] = obj

        model_cfg = _parse_model_config(read(MODEL_CONFIG))

        objects: List[ModelObject] = []
        object_of_key: Dict[tuple, int] = {}
        build_items: List[BuildItem] = []
        build = root.find(ns.q(ns.CORE, "build"))
        for item in (build.findall(ns.q(ns.CORE, "item")) if build is not None else []):
            oid = item.get("objectid")
            key = (ROOT_MODEL, oid)
            if key not in elements:
                continue
            transform = Transform.parse(item.get("transform"))
            printable = item.get("printable", "1") not in ("0", "false", "False")
            mesh_key, chain = _resolve_mesh(key, elements)
            if mesh_key is None:
                continue
            for t in chain:  # component transforms (innermost first) -> item
                transform = t.compose(transform)
            if mesh_key not in object_of_key:
                obj_el = elements[mesh_key]
                mesh = obj_el.find(ns.q(ns.CORE, "mesh"))
                tris = mesh.find(ns.q(ns.CORE, "triangles"))
                mo = ModelObject(
                    id=mesh_key[1],
                    index=len(objects) + 1,
                    mesh=mesh,
                    name=obj_el.get("name", ""),
                    triangle_count=len(tris) if tris is not None else 0,
                )
                cfg_obj = model_cfg.get(mesh_key[1]) if mesh_key[0] == ROOT_MODEL else None
                _apply_model_config(mo, cfg_obj)
                object_of_key[mesh_key] = len(objects)
                objects.append(mo)
            build_items.append(BuildItem(object_of_key[mesh_key], transform, printable))

        project = PrusaProject(
            source_path=path,
            producer=producer,
            objects=objects,
            build_items=build_items,
            config=config,
            title=title,
        )
        project.bed_size, project.bed_origin = _bed_geometry(config)

        data = read(CUSTOM_GCODE)
        if data:
            project.custom_gcodes = _parse_custom_gcodes(data)
        data = read(WIPE_TOWER_INFO)
        if data:
            project.wipe_towers = _parse_wipe_towers(data)
        data = read(LAYER_CONFIG_RANGES)
        if data:
            project.layer_ranges = _parse_layer_ranges(data)
        data = read(LAYER_HEIGHTS_PROFILE)
        if data:
            project.layer_heights = _parse_layer_heights(data)
        project.thumbnail = read(THUMBNAIL)
        project.has_cut_info = read(CUT_INFORMATION) is not None
        project.has_full_spectrum = read(FULL_SPECTRUM) is not None

    _assign_beds(project, _generator_version(producer))
    return project


def _resolve_mesh(key: tuple, elements: dict, depth: int = 0):
    """Follow single-component indirection down to the object holding the mesh.

    Returns (mesh_key, [component transforms, innermost first]). PrusaSlicer's extra
    instances are objects with one identity component pointing at the first instance.
    """
    obj = elements.get(key)
    if obj is None or depth > 16:
        return None, []
    if obj.find(ns.q(ns.CORE, "mesh")) is not None:
        return key, []
    comps = obj.find(ns.q(ns.CORE, "components"))
    if comps is None:
        return None, []
    comp_list = comps.findall(ns.q(ns.CORE, "component"))
    if len(comp_list) != 1:
        # A generic multi-component 3mf: not something PrusaSlicer writes.
        raise ValueError(
            "This 3mf uses multi-component objects, which PrusaSlicer does not write. "
            "Open it in PrusaSlicer and save it as a project first."
        )
    comp = comp_list[0]
    p = comp.get(ns.q(ns.PRODUCTION, "path"))
    target = (_normalize_path(p) if p else key[0], comp.get("objectid"))
    mesh_key, chain = _resolve_mesh(target, elements, depth + 1)
    return mesh_key, chain + [Transform.parse(comp.get("transform"))]


def _metadata(el) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for md in el.findall("metadata"):
        k, v = md.get("key"), md.get("value")
        if k is not None and v is not None:
            out[k] = v
    return out


def _parse_model_config(data: Optional[bytes]) -> dict:
    """Slic3r_PE_model.config -> {object id: (object metadata, [volume elements])}."""
    if not data:
        return {}
    root = _parse(data)
    out = {}
    for obj in root.findall("object"):
        oid = obj.get("id")
        if oid is not None:
            out[oid] = obj
    return out


def _apply_model_config(mo: ModelObject, obj_el) -> None:
    n_tris = mo.triangle_count
    if obj_el is None:
        mo.volumes = [Volume(0, max(n_tris - 1, 0), name=mo.name)]
        return
    meta = _metadata(obj_el)
    mo.name = meta.get("name", mo.name)
    mo.extruder = _int_or_none(meta.get("extruder"))
    mo.settings = {k: v for k, v in meta.items() if k not in _OBJECT_META_KEYS}
    for vol_el in obj_el.findall("volume"):
        first = _int_or_none(vol_el.get("firstid"))
        last = _int_or_none(vol_el.get("lastid"))
        if first is None or last is None or last < first or last >= n_tris:
            continue
        vm = _metadata(vol_el)
        vtype = vm.get("volume_type") or ("ParameterModifier" if vm.get("modifier") == "1" else "ModelPart")
        vol = Volume(firstid=first, lastid=last, name=vm.get("name", ""), volume_type=vtype)
        vol.extruder = _int_or_none(vm.get("extruder"))
        if vm.get("matrix"):
            t = matrix16_to_transform(vm["matrix"])
            if t is not None:
                vol.matrix = t
                vol.has_matrix = True
        vol.settings = {k: v for k, v in vm.items() if k not in _VOLUME_META_KEYS}
        vol.source = {k: v for k, v in vm.items() if k.startswith("source_")}
        for child in vol_el:
            tag = child.tag if isinstance(child.tag, str) else ""
            if tag == ns.q(ns.SLIC3RPE, "text") or tag.endswith("}text") or tag == "slic3rpe:text":
                vol.text = dict(child.attrib)
            elif tag == ns.q(ns.SLIC3RPE, "shape") or tag.endswith("}shape") or tag == "slic3rpe:shape":
                vol.shape = dict(child.attrib)
        mo.volumes.append(vol)
    if not mo.volumes:
        mo.volumes = [Volume(0, max(n_tris - 1, 0), name=mo.name)]
    mo.volumes.sort(key=lambda v: v.firstid)


def _parse_custom_gcodes(data: bytes) -> Dict[int, BedGCodes]:
    root = _parse_fragments(data)
    out: Dict[int, BedGCodes] = {}
    for block in root.findall("custom_gcodes_per_print_z"):
        bed = (_int_or_none(block.get("bed_idx")) or 0) + 1
        bg = BedGCodes()
        mode = block.find("mode")
        if mode is not None and mode.get("value"):
            bg.mode = mode.get("value")
        for code in block.findall("code"):
            try:
                z = float(code.get("print_z", ""))
            except ValueError:
                continue
            ctype = _int_or_none(code.get("type"))
            extra = code.get("extra", "") or ""
            if ctype is None:  # files older than 2.4: the type is implied by the G-code
                g = code.get("gcode", "")
                ctype = {"M600": 0, "M601": 1, "tool_change": 2}.get(g, 4)
                if ctype == 4:
                    extra = g
            bg.items.append(CustomGCode(z, ctype, _int_or_none(code.get("extruder")) or 1,
                                        code.get("color", "") or "", extra))
        out[bed] = bg
    return out


def _parse_wipe_towers(data: bytes) -> Dict[int, tuple]:
    root = _parse_fragments(data)
    out: Dict[int, tuple] = {}
    for block in root.findall("wipe_tower_information"):
        try:
            x = float(block.get("position_x", ""))
            y = float(block.get("position_y", ""))
        except ValueError:
            continue
        try:
            rot = float(block.get("rotation_deg", "0"))
        except ValueError:
            rot = 0.0
        out[(_int_or_none(block.get("bed_idx")) or 0) + 1] = (x, y, rot)
    return out


def _parse_layer_ranges(data: bytes) -> Dict[int, List[LayerRange]]:
    root = _parse(data)
    out: Dict[int, List[LayerRange]] = {}
    for obj in root.iter("object"):
        idx = _int_or_none(obj.get("id"))
        if idx is None:
            continue
        ranges = []
        for rng in obj.findall("range"):
            try:
                lo, hi = float(rng.get("min_z", "")), float(rng.get("max_z", ""))
            except ValueError:
                continue
            settings = {}
            for opt in rng.findall("option"):
                key = opt.get("opt_key")
                if key:
                    settings[key] = (opt.text or "").strip()
            ranges.append(LayerRange(lo, hi, settings))
        if ranges:
            out[idx] = ranges
    return out


def _parse_layer_heights(data: bytes) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        head, _, profile = line.partition("|")
        _, _, idx = head.partition("=")
        i = _int_or_none(idx.strip())
        if i is not None and profile.strip():
            out[i] = profile.strip()
    return out


# -- beds ---------------------------------------------------------------------------------

def _bed_geometry(config: PrusaConfig) -> tuple:
    """((width, depth), (min_x, min_y)) of the PrusaSlicer bed; MK4-sized if unknown."""
    xs, ys = [], []
    for p in config.vector("bed_shape") or []:
        try:
            x, y = p.split("x")
            xs.append(float(x))
            ys.append(float(y))
        except ValueError:
            pass
    if len(xs) < 3:
        return (250.0, 210.0), (0.0, 0.0)
    return (max(xs) - min(xs), max(ys) - min(ys)), (min(xs), min(ys))


def _generator_version(producer: str) -> Optional[tuple]:
    if not producer.startswith("PrusaSlicer-"):
        return None
    ver = producer[len("PrusaSlicer-"):]
    nums = []
    for part in ver.replace("-", ".").replace("+", ".").split("."):
        if part.isdigit():
            nums.append(int(part))
        else:
            break
    return tuple(nums) if nums else None


def grid_coords(index: int) -> tuple:
    """PrusaSlicer 2.9 bed grid position of a 0-based bed index (BedsGrid::index2grid_coords,
    first quadrant): beds fill growing squares, 0 -> (0,0), 1 -> (1,0), 2 -> (0,1),
    3 -> (1,1), 4 -> (2,0), 5 -> (2,1), 6 -> (0,2), ..."""
    if index == 0:
        return (0, 0)
    i = index + 1
    a = 1
    while (a + 1) * (a + 1) < i:
        a += 1
    i -= a * a
    x, y = a, a
    if i <= a:
        y = i - 1
    else:
        x = i - a - 1
    return (x, y)


def bed_translation(index: int, bed_size: tuple, legacy: bool = False) -> tuple:
    """World offset of 0-based bed ``index`` (MultipleBeds::get_bed_translation)."""
    if index == 0:
        return (0.0, 0.0)
    w, d = bed_size
    if legacy:
        return (index * (w + w * 0.2), 0.0)
    gap = min(100.0, math.hypot(w, d) * 0.3)
    x, y = grid_coords(index)
    return (x * (w + gap), y * (d + gap))


def _item_center(project: PrusaProject, item: BuildItem) -> Optional[tuple]:
    """XY centre of an instance's bounding box in world coordinates."""
    mo = project.objects[item.object_index]
    verts = mo.mesh.find(ns.q(ns.CORE, "vertices"))
    if verts is None:
        return None
    minx = miny = math.inf
    maxx = maxy = -math.inf
    t = item.transform
    for v in verts:
        try:
            x, y, _z = t.apply_point(float(v.get("x")), float(v.get("y")), float(v.get("z")))
        except (TypeError, ValueError):
            continue
        minx, maxx = min(minx, x), max(maxx, x)
        miny, maxy = min(miny, y), max(maxy, y)
    if minx is math.inf:
        return None
    return ((minx + maxx) / 2.0, (miny + maxy) / 2.0)


def _assign_beds(project: PrusaProject, version: Optional[tuple]) -> None:
    """Work out which PrusaSlicer bed each instance sits on.

    PrusaSlicer 2.9+ keeps no bed membership in the file: an instance belongs to the bed
    whose area contains it, beds being laid out on a grid (MultipleBeds.cpp). Projects
    from older versions only ever had one bed, but objects arranged on PrusaSlicer's
    legacy virtual beds (1.2 x bed width along X) are honoured the same way 2.9 does."""
    legacy = version is not None and version < (2, 9)
    size, origin = project.bed_size, project.bed_origin
    candidates = [(i, bed_translation(i, size, legacy)) for i in range(MAX_BEDS)]
    for item in project.build_items:
        c = _item_center(project, item)
        item.bed = 1
        if c is None:
            continue
        for i, (tx, ty) in candidates:
            x0, y0 = origin[0] + tx, origin[1] + ty
            if x0 <= c[0] <= x0 + size[0] and y0 <= c[1] <= y0 + size[1]:
                item.bed = i + 1
                break
