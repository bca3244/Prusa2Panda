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
