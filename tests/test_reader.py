import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fixtures import Obj, Part, build_3mf  # noqa: E402

from prusa2panda.reader import bed_translation, read_prusa_3mf  # noqa: E402


def test_basic_read(tmp_path):
    p = build_3mf(tmp_path / "a.3mf", [
        Obj("box", parts=[
            Part("body", extruder=1, paint={0: {"mmu_segmentation": "8"}}),
            Part("mod", offset=(2, 2, 2), scale=2, volume_type="ParameterModifier",
                 settings={"fill_density": "50%"}),
        ], instances=[(50, 50, 0), (100, 50, 0)], settings={"perimeters": "4"}),
    ])
    pr = read_prusa_3mf(p)
    assert pr.producer == "PrusaSlicer-2.9.2"
    assert len(pr.objects) == 1
    assert len(pr.build_items) == 2
    obj = pr.objects[0]
    assert obj.name == "box"
    assert obj.settings == {"perimeters": "4"}
    assert [(v.firstid, v.lastid) for v in obj.volumes] == [(0, 11), (12, 23)]
    assert obj.volumes[1].volume_type == "ParameterModifier"
    assert obj.volumes[1].settings == {"fill_density": "50%"}
    assert obj.volumes[0].extruder == 1
    assert obj.volumes[1].matrix.m[9:] == (2.0, 2.0, 2.0)
    assert obj.volumes[1].matrix.m[0] == 2.0
    assert pr.painted_triangles == 1
    assert pr.extruder_colour(1) == "#FF0000"
    assert pr.extruder_colour(2) == "#00FF00"
    assert pr.bed_ids == [1]


def test_beds_from_positions(tmp_path):
    # MK4: 250 x 210 bed; 2.9 grid gap = min(100, 0.3 * diag) = 97.6
    tx, ty = bed_translation(1, (250.0, 210.0))
    assert round(tx, 3) == round(250 + 0.3 * (250 ** 2 + 210 ** 2) ** 0.5, 3) and ty == 0
    p = build_3mf(tmp_path / "b.3mf", [
        Obj("a", instances=[(50, 50, 0)]),
        Obj("b", instances=[(50 + tx, 50, 0)]),
    ])
    pr = read_prusa_3mf(p)
    assert [bi.bed for bi in pr.build_items] == [1, 2]
    assert pr.bed_ids == [1, 2]


def test_custom_gcodes_and_wipe_tower(tmp_path):
    cg = ('<?xml version="1.0" encoding="utf-8"?>\n'
          '<custom_gcodes_per_print_z bed_idx="0">\n'
          '<code print_z="1.2" type="0" extruder="1" color="#00FF00" extra="" gcode="M600"/>\n'
          '<mode value="SingleExtruder"/>\n</custom_gcodes_per_print_z>\n'
          '<custom_gcodes_per_print_z bed_idx="1">\n'
          '<code print_z="2" type="1" extruder="1" color="" extra="pause" gcode="M601"/>\n'
          '<mode value="SingleExtruder"/>\n</custom_gcodes_per_print_z>\n')
    wt = ('<?xml version="1.0" encoding="utf-8"?>\n'
          '<wipe_tower_information bed_idx="0" position_x="170" position_y="140" rotation_deg="0"/>\n')
    p = build_3mf(tmp_path / "c.3mf", [Obj()], custom_gcodes=cg, wipe_tower=wt,
                  layer_heights="object_id=1|0;0.2;5;0.1\n")
    pr = read_prusa_3mf(p)
    assert set(pr.custom_gcodes) == {1, 2}
    assert pr.custom_gcodes[1].items[0].print_z == 1.2
    assert pr.custom_gcodes[2].items[0].type == 1
    assert pr.wipe_towers[1] == (170.0, 140.0, 0.0)
    assert pr.layer_heights == {1: "0;0.2;5;0.1"}
