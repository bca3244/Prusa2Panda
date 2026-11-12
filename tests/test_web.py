"""The web version's Python side: preset tables, the table-backed install, web.convert."""

import json
import os
import sys
import zipfile

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

from fixtures import Obj, Part, build_3mf  # noqa: E402

import gen_preset_table  # noqa: E402
from prusa2panda import web  # noqa: E402
from prusa2panda.preset_table import TableInstall  # noqa: E402

P1S = "Bambu Lab P1S 0.4 nozzle"
X1C = "Bambu Lab X1 Carbon 0.4 nozzle"


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def profiles(tmp_path):
    """A small BBL vendor tree laid out like resources/profiles (inherits, abstract bases)."""
    root = tmp_path / "profiles"
    vendor = {"name": "Bambulab", "machine_model_list": [], "machine_list": [], "process_list": [],
              "filament_list": []}

    def add(kind, name, data, sub=None):
        sub = sub or f"{kind}/{name}.json"
        _write(root / "BBL" / sub, dict(data, name=name))
        vendor[f"{kind}_list"].append({"name": name, "sub_path": sub})

    add("machine", "fdm_bbl_3dp_001_common", {
        "instantiation": "false", "printable_area": ["0x0", "256x0", "256x256", "0x256"],
        "printable_height": "250", "nozzle_diameter": ["0.4"], "extruder_type": ["Direct Drive"],
        "printer_extruder_variant": ["Direct Drive Standard", "Direct Drive High Flow"],
        "default_nozzle_volume_type": ["Standard"], "default_print_profile": "0.20mm Standard @BBL X1C",
    })
    for name, model in ((P1S, "Bambu Lab P1S"), (X1C, "Bambu Lab X1 Carbon")):
        add("machine", name, {"inherits": "fdm_bbl_3dp_001_common", "printer_model": model,
                              "printer_variant": "0.4", "default_filament_profile": ["Bambu PLA Basic @BBL X1C"]})
    add("process", "fdm_process_common", {"instantiation": "false", "layer_height": "0.2",
                                          "print_extruder_variant": ["Direct Drive Standard", "Direct Drive High Flow"]})
    add("process", "0.20mm Standard @BBL X1C", {"inherits": "fdm_process_common",
                                                "compatible_printers": [P1S, X1C]})
    add("process", "0.16mm Optimal @BBL X1C", {"inherits": "fdm_process_common", "layer_height": "0.16",
                                               "compatible_printers": [X1C]})
    add("filament", "fdm_filament_common", {"instantiation": "false", "filament_diameter": ["1.75"]})
    for name, ftype, fid in (("Bambu PLA Basic @BBL X1C", "PLA", "GFA00"), ("Generic PETG", "PETG", "GFG99"),
                             ("Generic PLA", "PLA", "GFL99"), ("Bambu PETG HF @BBL X1C", "PETG", "GFG02")):
        add("filament", name, {"inherits": "fdm_filament_common", "filament_type": [ftype], "filament_id": fid,
                               "compatible_printers": [P1S, X1C]})
    _write(root / "BBL.json", vendor)
    return root


@pytest.fixture
def table_dir(profiles, tmp_path):
    out = tmp_path / "presets"
    gen_preset_table.generate("bambu", str(profiles), "02.09.01.70", str(out))
    return out


@pytest.fixture
def project(tmp_path):
    # Extruder 1 is PLA, extruder 2 PETG (fixtures.DEFAULT_CONFIG).
    return str(build_3mf(tmp_path / "in.3mf", [
        Obj("a", parts=[Part("pla", extruder=1), Part("petg", extruder=2, offset=(12, 0, 0))]),
    ]))


def _cfg(path):
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read("Metadata/project_settings.config"))


def test_table_has_resolved_subset(table_dir):
    idx = json.loads((table_dir / "index.json").read_text())
    assert idx["version"] == "02.09.01.70"
    (vendor,) = idx["vendors"]
    assert vendor["name"] == "BBL" and vendor["title"] == "Bambu Lab"
    assert vendor["printers"] == [P1S, X1C]  # the abstract base is not a printer
    t = json.loads((table_dir / vendor["file"]).read_text())
    m = t["machine"][P1S]
    assert m["printable_area"][2] == "256x256" and m["printer_model"] == "Bambu Lab P1S"
    assert "fdm_process_common" not in t["process"]
    assert t["process"]["0.16mm Optimal @BBL X1C"]["layer_height"] == "0.16"
    assert t["filament"]["Generic PETG"]["filament_diameter"] == ["1.75"]
    inst = TableInstall("bambu", [t])
    assert inst.processes_for(P1S) == ["0.20mm Standard @BBL X1C"]
    assert inst.resolve("machine", P1S)["printable_height"] == "250"
    with pytest.raises(ValueError):
        TableInstall("orca", [t])


def _request(project, table_dir, out, **kw):
    req = {"input": project, "output": str(out), "slicer": "bambu",
           "tables": [str(table_dir / "BBL.json")], "printer": "preset", "printer_name": P1S,
           "process": "0.20mm Standard @BBL X1C",
           "filaments": [{"name": "Bambu PLA Basic @BBL X1C", "locked": False}] * 2}
    req.update(kw)
    return json.dumps(req)


def test_presets_by_name_only(project, table_dir, tmp_path):
    r = json.loads(web.convert(_request(project, table_dir, tmp_path / "out.3mf")))
    assert r["names"] == ["out.3mf"]
    cfg = _cfg(tmp_path / "out.3mf")
    assert cfg["printer_settings_id"] == P1S
    assert cfg["print_settings_id"] == "0.20mm Standard @BBL X1C"
    # Extruder 2 is PETG in PrusaSlicer: the PLA default becomes the Generic PETG preset.
    assert cfg["filament_settings_id"] == ["Bambu PLA Basic @BBL X1C", "Generic PETG"]
    assert cfg["inherits_group"] == ["", "", "", ""]
    assert cfg["different_settings_to_system"] == ["", "", "", ""]
    assert cfg["filament_ids"] == ["GFA00", "GFG99"]
    assert cfg["filament_colour"] == ["#FF0000", "#00FF00"]
    assert cfg["printable_area"] == ["0x0", "256x0", "256x256", "0x256"]


def test_explicit_filament_is_kept(project, table_dir, tmp_path):
    fils = [{"name": "Bambu PLA Basic @BBL X1C", "locked": False},
            {"name": "Bambu PETG HF @BBL X1C", "locked": True}]
    web.convert(_request(project, table_dir, tmp_path / "out.3mf", filaments=fils))
    assert _cfg(tmp_path / "out.3mf")["filament_settings_id"] == ["Bambu PLA Basic @BBL X1C", "Bambu PETG HF @BBL X1C"]
    # Locked even when the material differs from the PrusaSlicer one.
    fils = [{"name": "Generic PETG", "locked": True}, {"name": "Generic PETG", "locked": True}]
    web.convert(_request(project, table_dir, tmp_path / "out2.3mf", filaments=fils))
    assert _cfg(tmp_path / "out2.3mf")["filament_settings_id"] == ["Generic PETG", "Generic PETG"]


def test_prusa_settings_only_translated_keys(project, table_dir, tmp_path):
    web.convert(_request(project, table_dir, tmp_path / "out.3mf", settings="prusa"))
    cfg = _cfg(tmp_path / "out.3mf")
    diffs = cfg["different_settings_to_system"]
    assert "wall_loops" in diffs[0].split(";")
    assert diffs[-1] == ""  # the printer stays the system preset
    assert cfg["inherits_group"] == ["", "", "", ""]


def test_prusa_printer_and_geometry_only(project, table_dir, tmp_path):
    web.convert(_request(project, table_dir, tmp_path / "p.3mf", printer="prusa"))
    cfg = _cfg(tmp_path / "p.3mf")
    assert cfg["printable_area"] == ["0x0", "250x0", "250x210", "0x210"]
    web.convert(_request(project, table_dir, tmp_path / "g.3mf", settings="none"))
    with zipfile.ZipFile(tmp_path / "g.3mf") as z:
        assert "Metadata/project_settings.config" not in z.namelist()


def test_preset_file_printer(project, table_dir, tmp_path):
    preset = tmp_path / "printer.json"
    preset.write_text(json.dumps({"type": "machine", "name": X1C, "inherits": "fdm_bbl_3dp_001_common"}))
    assert web.preset_file_printer(str(preset)) == X1C
    web.convert(_request(project, table_dir, tmp_path / "out.3mf", printer="file", printer_file=str(preset)))
    assert _cfg(tmp_path / "out.3mf")["printer_settings_id"] == X1C


def test_inspect(project):
    info = json.loads(web.inspect(project))
    assert info["beds"] == [1]
    assert [u["extruder"] for u in info["used"]] == [1, 2]
    assert info["used"][1]["generic_type"] == "PETG"
    assert info["suggested_map"] == {"1": 1, "2": 2}
