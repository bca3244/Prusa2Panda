import json
import os
import re
import sys
import zipfile

import pytest
from lxml import etree

sys.path.insert(0, os.path.dirname(__file__))

from fixtures import Obj, Part, build_3mf  # noqa: E402

from prusa2panda.convert import BEDS_SPLIT, PRINTER_PRUSA, convert_file, used_extruders  # noqa: E402
from prusa2panda.reader import read_prusa_3mf  # noqa: E402

SAMPLES = os.environ.get("PRUSA2PANDA_SAMPLES", "")


def _open(path):
    z = zipfile.ZipFile(path)
    for n in z.namelist():
        if n.endswith((".model", ".rels", ".xml")) or n.endswith("model_settings.config"):
            etree.fromstring(z.read(n))  # well-formed
    return z


def _settings(z):
    return etree.fromstring(z.read("Metadata/model_settings.config"))


def _meta(el, key):
    m = el.find(f"metadata[@key='{key}']")
    return None if m is None else m.get("value")


def test_parts_paint_and_map(tmp_path):
    src = build_3mf(tmp_path / "in.3mf", [
        Obj("box", parts=[
            Part("body", extruder=2, paint={0: {"mmu_segmentation": "8"}, 1: {"custom_supports": "4"}}),
            Part("mod", offset=(2, 2, 2), scale=2, volume_type="ParameterModifier",
                 settings={"fill_density": "50%"}),
        ], settings={"perimeters": "4"}),
    ])
    out = tmp_path / "out.3mf"
    res = convert_file(src, str(out), printer=PRINTER_PRUSA, config_dir=str(tmp_path / "none"))
    assert res.stats.objects_written == 1
    assert res.stats.parts_written == 2
    z = _open(out)
    names = z.namelist()
    assert "3D/_rels/3dmodel.model.rels" in names
    root = z.read("3D/3dmodel.model").decode()
    assert re.search(r'<metadata name="Application">BambuStudio-', root)
    part_file = [n for n in names if n.startswith("3D/Objects/")]
    assert len(part_file) == 1
    body = z.read(part_file[0]).decode()
    assert 'paint_color="' in body and 'paint_supports="4"' in body
    st = _settings(z)
    obj = st.find("object")
    assert _meta(obj, "name") == "box"
    assert _meta(obj, "wall_loops") == "4"
    parts = obj.findall("part")
    assert [p.get("subtype") for p in parts] == ["normal_part", "modifier_part"]
    ps = json.loads(z.read("Metadata/project_settings.config"))
    assert all(isinstance(v, (str, list)) for v in ps.values())
    assert ps["printer_settings_id"]


def test_used_extruders_and_compaction(tmp_path):
    src = build_3mf(tmp_path / "in.3mf", [
        Obj("a", parts=[Part("p", extruder=3)]),
        Obj("b", parts=[Part("q")], extruder=5),
    ], config={"nozzle_diameter": "0.4,0.4,0.4,0.4,0.4",
               "extruder_colour": '"#111111";"#222222";"#333333";"#444444";"#555555"',
               "filament_type": "PLA;PLA;PLA;PLA;PLA"})
    assert used_extruders(read_prusa_3mf(src)) == [3, 5]
    out = tmp_path / "out.3mf"
    res = convert_file(src, str(out), settings=False)
    assert res.stats.filament_map == {3: 1, 5: 2}
    objs = _settings(_open(out)).findall("object")
    # single-part objects carry their extruder on the object
    assert [_meta(o, "extruder") for o in objs] == ["1", "2"]


def test_beds_to_plates_and_split(tmp_path):
    # second bed: 250 + gap(97.6) to the right on a 2.9 grid
    src = build_3mf(tmp_path / "in.3mf", [
        Obj("a", instances=[(50, 50, 0)]),
        Obj("b", instances=[(50 + 347.6, 50, 0)]),
    ])
    pr = read_prusa_3mf(src)
    assert pr.bed_ids == [1, 2]
    out = tmp_path / "out.3mf"
    convert_file(src, str(out), settings=False)
    st = _settings(_open(out))
    assert len(st.findall("plate")) == 2
    res = convert_file(src, str(tmp_path / "s.3mf"), settings=False, bed_layout=BEDS_SPLIT)
    assert [os.path.basename(p) for p in res.outputs] == ["s_bed1.3mf", "s_bed2.3mf"]
    with pytest.raises(ValueError):
        convert_file(src, str(tmp_path / "x.3mf"), beds=[7], settings=False)


@pytest.mark.skipif(not SAMPLES, reason="set PRUSA2PANDA_SAMPLES to a folder of PrusaSlicer .3mf files")
def test_samples(tmp_path):
    files = [os.path.join(SAMPLES, f) for f in sorted(os.listdir(SAMPLES))]
    files = [f for f in files if os.path.isfile(f) and zipfile.is_zipfile(f)]
    for i, f in enumerate(files):
        try:
            zipfile.ZipFile(f).getinfo("Metadata/Slic3r_PE_model.config")
        except KeyError:
            continue
        out = tmp_path / f"o{i}.3mf"
        res = convert_file(f, str(out), printer=PRINTER_PRUSA, config_dir=str(tmp_path / "none"))
        assert res.stats.objects_written > 0
        _open(out)


# -- regressions from the code review --------------------------------------------------------

def _extruder_project(tmp_path):
    cfg = {"nozzle_diameter": "0.4,0.4,0.4,0.4,0.4",
           "extruder_colour": '"#111111";"#222222";"#333333";"#444444";"#555555"',
           "filament_type": "PLA;PLA;PLA;PLA;PLA"}
    ranges = ('<?xml version="1.0" encoding="utf-8"?><objects><object id="1"><range min_z="0" max_z="2">'
              '<option opt_key="extruder">2</option></range></object></objects>')
    gc = ('<?xml version="1.0" encoding="utf-8"?><custom_gcodes_per_print_z bed_idx="0">'
          '<code print_z="2.4" type="2" extruder="4" color="" extra="" gcode="tool_change"/>'
          '<code print_z="5" type="0" extruder="1" color="#123456" extra="" gcode="M600"/>'
          '<mode value="MultiAsSingle"/></custom_gcodes_per_print_z>')
    return build_3mf(tmp_path / "in.3mf", [
        Obj("a", parts=[Part("p")], extruder=3, settings={"support_material": "1",
                                                         "support_material_extruder": "5"}),
    ], config=cfg, layer_ranges=ranges, custom_gcodes=gc)


def test_every_extruder_reference_gets_a_slot(tmp_path):
    src = _extruder_project(tmp_path)
    # object 3, layer range 2, tool change 4, per-object support 5; the colour change on 1 is unused
    assert used_extruders(read_prusa_3mf(src)) == [2, 3, 4, 5]
    out = tmp_path / "out.3mf"
    res = convert_file(src, str(out), printer=PRINTER_PRUSA, config_dir=str(tmp_path / "none"))
    assert res.stats.filament_map == {2: 1, 3: 2, 4: 3, 5: 4}
    z = _open(out)
    obj = _settings(z).find("object")
    assert _meta(obj, "extruder") == "2" and _meta(obj, "support_filament") == "4"
    gcodes = z.read("Metadata/custom_gcode_per_layer.xml").decode()
    assert 'extruder="3"' in gcodes and "M600" not in gcodes  # unused extruder's colour change skipped
    assert any("unused extruder 1" in w for w in res.stats.warnings)


def test_orca_filament_ids_remapped(tmp_path):
    src = build_3mf(tmp_path / "in.3mf", [
        Obj("a", parts=[Part("p", settings={"perimeter_extruder": "3", "solid_infill_extruder": "3",
                                            "infill_extruder": "3"})], extruder=3),
    ], config={"nozzle_diameter": "0.4,0.4,0.4", "extruder_colour": '"#1";"#2";"#3"',
               "filament_type": "PLA;PLA;PLA"})
    out = tmp_path / "out.3mf"
    convert_file(src, str(out), printer=PRINTER_PRUSA, slicer="orca", config_dir=str(tmp_path / "none"))
    part = _settings(_open(out)).find("object/part")
    for k in ("outer_wall_filament_id", "inner_wall_filament_id", "sparse_infill_filament_id",
              "internal_solid_filament_id", "top_surface_filament_id", "bottom_surface_filament_id"):
        assert _meta(part, k) == "1", k


def test_legacy_virtual_beds(tmp_path):
    # PrusaSlicer 2.8: virtual bed 2 starts at 1.2 x 250 = 300 mm
    src = build_3mf(tmp_path / "in.3mf", [
        Obj("a", instances=[(50, 50, 0)]), Obj("b", instances=[(350, 50, 0)]),
    ], producer="PrusaSlicer-2.8.1")
    pr = read_prusa_3mf(src)
    assert pr.legacy_beds and [bi.bed for bi in pr.build_items] == [1, 2]
    out = tmp_path / "out.3mf"
    convert_file(src, str(out), settings=False, config_dir=str(tmp_path / "none"))
    root = etree.fromstring(_open(out).read("3D/3dmodel.model"))
    xs = [float(i.get("transform").split()[9]) for i in root.iter("{*}item")]
    # both objects sit 50 mm into their own bed, so they land at the same plate-local x:
    # exactly one plate stride apart (1.2 x the 256 mm default plate)
    assert abs((xs[1] - xs[0]) - 1.2 * 256) < 1e-6


def test_post_process_not_repeated_per_variant():
    from prusa2panda import keymap as K
    out = K.convert_print({"post_process": '"/a.py";"/b.py"'}, "bambu", [], 2)
    assert out["post_process"] == ["/a.py", "/b.py"]
    assert K.convert_print({"post_process": ""}, "orca", [], 3)["post_process"] == []


def test_malformed_vertex_not_written(tmp_path):
    src = build_3mf(tmp_path / "in.3mf", [Obj("a")])
    bad = tmp_path / "bad.3mf"
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(bad, "w") as zo:
        for n in zi.namelist():
            data = zi.read(n)
            if n == "3D/3dmodel.model":
                data = data.replace(b'<vertex x="0', b'<vertex x="0&quot;/&gt;&lt;evil a=&quot;1', 1)
            zo.writestr(n, data)
    out = tmp_path / "out.3mf"
    res = convert_file(str(bad), str(out), settings=False, config_dir=str(tmp_path / "none"))
    assert any("malformed vertices" in w for w in res.stats.warnings)
    assert b"evil" not in b"".join(zipfile.ZipFile(out).read(n) for n in zipfile.ZipFile(out).namelist())


def _colour_change_project(tmp_path, extruders=None):
    gc = ('<?xml version="1.0" encoding="utf-8"?><custom_gcodes_per_print_z bed_idx="0">'
          '<code print_z="11.8" type="0" extruder="1" color="#FF8000" extra="" gcode="M600"/>'
          '<code print_z="15" type="0" extruder="1" color="#FF0000" extra="" gcode="M600"/>'
          '<mode value="SingleExtruder"/></custom_gcodes_per_print_z>')
    parts = [Part("p", extruder=e) for e in (extruders or [None])]
    return build_3mf(tmp_path / "in.3mf", [Obj("a", parts=parts)], custom_gcodes=gc,
                     config={"color_change_gcode": "M600\\nG1 E0.3 F1500"})


def test_colour_change_becomes_filament_change(tmp_path):
    src = _colour_change_project(tmp_path)
    out = tmp_path / "out.3mf"
    convert_file(src, str(out), settings=False, config_dir=str(tmp_path / "none"))
    xml = _open(out).read("Metadata/custom_gcode_per_layer.xml").decode()
    # #FF0000 is extruder 1's own colour: back to slot 1; #FF8000 gets a new slot 2
    assert 'top_z="11.8" type="2" extruder="2"' in xml
    assert 'top_z="15" type="2" extruder="1"' in xml
    assert 'value="MultiAsSingle"' in xml


def test_colour_change_slots_in_settings(tmp_path):
    src = _colour_change_project(tmp_path)
    out = tmp_path / "out.3mf"
    convert_file(src, str(out), printer="auto", slicer="bambu", config_dir=str(tmp_path / "none"))
    # no install: the PrusaSlicer printer is kept, so its own colour change G-code runs
    xml = _open(out).read("Metadata/custom_gcode_per_layer.xml").decode()
    assert 'type="4"' in xml and "M600&#10;G1 E0.3 F1500" in xml
    ps = json.loads(_open(out).read("Metadata/project_settings.config"))
    assert len(ps["filament_colour"]) == 1


def test_colour_change_in_multi_filament_print_pauses(tmp_path):
    src = _colour_change_project(tmp_path, extruders=[1, 2])
    out = tmp_path / "out.3mf"
    res = convert_file(src, str(out), settings=False, config_dir=str(tmp_path / "none"))
    xml = _open(out).read("Metadata/custom_gcode_per_layer.xml").decode()
    assert xml.count('type="1"') == 2
    assert any("became pauses" in w for w in res.stats.warnings)
