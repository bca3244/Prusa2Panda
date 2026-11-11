"""Build small PrusaSlicer-style .3mf projects in memory for the tests.

The layout follows PrusaSlicer 2.9's own writer (3mf.cpp): one merged mesh per object,
volumes as triangle ranges in Slic3r_PE_model.config with their matrix, extra instances
as objects with a single component, and the bed / custom G-code metadata files.
"""

from __future__ import annotations

import zipfile
from typing import List, Optional

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
SLIC3RPE = "http://schemas.slic3r.org/3mf/2017/06"

# A unit cube: 8 vertices, 12 triangles (outward normals).
CUBE_V = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
CUBE_T = [
    (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
    (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
]

DEFAULT_CONFIG = {
    "bed_shape": "0x0,250x0,250x210,0x210",
    "printer_settings_id": "Original Prusa MK4S HF0.4 nozzle",
    "print_settings_id": "0.20mm SPEED @MK4S HF0.4",
    "filament_settings_id": '"Prusament PLA @PG";"Prusament PETG @PG"',
    "nozzle_diameter": "0.4,0.4",
    "extruder_colour": '"#FF0000";"#00FF00"',
    "filament_colour": '"#FF8000";"#0000FF"',
    "filament_type": "PLA;PETG",
    "layer_height": "0.2",
    "perimeters": "2",
    "fill_density": "15%",
    "fill_pattern": "gyroid",
    "support_material": "0",
    "color_change_gcode": "M600",
}


class Part:
    def __init__(self, name="part", offset=(0, 0, 0), scale=10.0, volume_type="ModelPart",
                 extruder: Optional[int] = None, paint: Optional[dict] = None,
                 settings: Optional[dict] = None, text: Optional[dict] = None,
                 shape: Optional[dict] = None):
        self.name = name
        self.offset = offset
        self.scale = scale
        self.volume_type = volume_type
        self.extruder = extruder
        self.paint = paint or {}  # triangle index -> {attr: code}
        self.settings = settings or {}
        self.text = text
        self.shape = shape

    def matrix16(self) -> str:
        s, (x, y, z) = self.scale, self.offset
        return f"{s} 0 0 {x} 0 {s} 0 {y} 0 0 {s} {z} 0 0 0 1"


class Obj:
    def __init__(self, name="obj", parts: Optional[List[Part]] = None, instances=((50, 50, 0),),
                 extruder: Optional[int] = None, settings: Optional[dict] = None):
        self.name = name
        self.parts = parts or [Part()]
        self.instances = list(instances)
        self.extruder = extruder
        self.settings = settings or {}


def build_3mf(path, objects: List[Obj], config: Optional[dict] = None, producer="PrusaSlicer-2.9.2",
              custom_gcodes: Optional[str] = None, wipe_tower: Optional[str] = None,
              layer_ranges: Optional[str] = None, layer_heights: Optional[str] = None) -> str:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(config or {})
    model = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE}" xmlns:slic3rpe="{SLIC3RPE}">',
        ' <metadata name="slic3rpe:Version3mf">1</metadata>',
        ' <metadata name="slic3rpe:MmPaintingVersion">1</metadata>',
        f' <metadata name="Application">{producer}</metadata>',
        " <resources>",
    ]
    mcfg = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    build = []
    next_id = 1
    for obj in objects:
        base_id = next_id
        model.append(f'  <object id="{base_id}" type="model">')
        model.append("   <mesh>\n    <vertices>")
        for p in obj.parts:
            for (x, y, z) in CUBE_V:
                s = p.scale
                model.append(f'     <vertex x="{x * s + p.offset[0]}" y="{y * s + p.offset[1]}" z="{z * s + p.offset[2]}"/>')
        model.append("    </vertices>\n    <triangles>")
        vbase = 0
        for p in obj.parts:
            for ti, (a, b, c) in enumerate(CUBE_T):
                attrs = "".join(f' slic3rpe:{k}="{v}"' for k, v in p.paint.get(ti, {}).items())
                model.append(f'     <triangle v1="{a + vbase}" v2="{b + vbase}" v3="{c + vbase}"{attrs}/>')
            vbase += len(CUBE_V)
        model.append("    </triangles>\n   </mesh>\n  </object>")
        for k in range(1, len(obj.instances)):
            model.append(f'  <object id="{base_id + k}" type="model"><components>'
                         f'<component objectid="{base_id}"/></components></object>')
        for k, (x, y, z) in enumerate(obj.instances):
            build.append(f'  <item objectid="{base_id + k}" transform="1 0 0 0 1 0 0 0 1 {x} {y} {z}" printable="1"/>')
        mcfg.append(f' <object id="{base_id}" instances_count="{len(obj.instances)}">')
        mcfg.append(f'  <metadata type="object" key="name" value="{obj.name}"/>')
        if obj.extruder:
            mcfg.append(f'  <metadata type="object" key="extruder" value="{obj.extruder}"/>')
        for k, v in obj.settings.items():
            mcfg.append(f'  <metadata type="object" key="{k}" value="{v}"/>')
        tri = 0
        for p in obj.parts:
            mcfg.append(f'  <volume firstid="{tri}" lastid="{tri + len(CUBE_T) - 1}">')
            tri += len(CUBE_T)
            mcfg.append(f'   <metadata type="volume" key="name" value="{p.name}"/>')
            if p.volume_type == "ParameterModifier":
                mcfg.append('   <metadata type="volume" key="modifier" value="1"/>')
            mcfg.append(f'   <metadata type="volume" key="volume_type" value="{p.volume_type}"/>')
            mcfg.append(f'   <metadata type="volume" key="matrix" value="{p.matrix16()}"/>')
            if p.extruder:
                mcfg.append(f'   <metadata type="volume" key="extruder" value="{p.extruder}"/>')
            for k, v in p.settings.items():
                mcfg.append(f'   <metadata type="volume" key="{k}" value="{v}"/>')
            if p.shape is not None:
                mcfg.append("   <slic3rpe:shape " + " ".join(f'{k}="{v}"' for k, v in p.shape.items()) + "/>")
            if p.text is not None:
                mcfg.append("   <slic3rpe:text " + " ".join(f'{k}="{v}"' for k, v in p.text.items()) + "/>")
            mcfg.append('   <mesh edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>')
            mcfg.append("  </volume>")
        mcfg.append(" </object>")
        next_id += len(obj.instances)
    model.append(" </resources>\n <build>")
    model.extend(build)
    model.append(" </build>\n</model>")
    mcfg.append("</config>")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types/>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships/>')
        z.writestr("3D/3dmodel.model", "\n".join(model))
        z.writestr("Metadata/Slic3r_PE_model.config", "\n".join(mcfg))
        z.writestr("Metadata/Slic3r_PE.config",
                   "; generated by PrusaSlicer 2.9.2\n\n" + "".join(f"; {k} = {v}\n" for k, v in sorted(cfg.items())))
        if custom_gcodes:
            z.writestr("Metadata/Prusa_Slicer_custom_gcode_per_print_z.xml", custom_gcodes)
        if wipe_tower:
            z.writestr("Metadata/Prusa_Slicer_wipe_tower_information.xml", wipe_tower)
        if layer_ranges:
            z.writestr("Metadata/Prusa_Slicer_layer_config_ranges.xml", layer_ranges)
        if layer_heights:
            z.writestr("Metadata/Slic3r_PE_layer_heights_profile.txt", layer_heights)
    return str(path)
