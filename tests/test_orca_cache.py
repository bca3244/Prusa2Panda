"""OrcaSlicer binary preset cache (<Vendor>.opc) reader, against a file built here."""

import json
import struct
import zlib

from prusa2panda import orca_cache as oc
from prusa2panda.bambu_install import SLICER_ORCA, BambuInstall


def _s(v: str) -> bytes:
    b = v.encode()
    return struct.pack("<Q", len(b)) + b


def _ss(vs) -> bytes:
    return struct.pack("<Q", len(vs)) + b"".join(_s(v) for v in vs)


KEYS = [("nozzle_temperature", oc.CO_INT | oc.VEC), ("filament_type", oc.CO_STRING | oc.VEC),
        ("printable_area", oc.CO_POINT | oc.VEC), ("layer_height", oc.CO_FLOAT),
        ("sparse_infill_density", oc.CO_PERCENT), ("wall_generator", oc.CO_ENUM),
        ("retract_when_changing_layer", oc.CO_BOOL | oc.VEC), ("printer_model", oc.CO_STRING),
        ("compatible_printers", oc.CO_STRING | oc.VEC), ("filament_flow_ratio", oc.CO_FLOAT | oc.VEC)]
ENUMS = ["", "arachne"]


def _entry(name, opts, inherits="", instantiation="true"):
    body = _s(name) + _s(f"x/{name}.json") + struct.pack("<I", len(opts))
    for key, raw in opts:
        body += struct.pack("<H", [k for k, _t in KEYS].index(key)) + raw
    body += _s(inherits) + _ss([]) + _s("") + _s(instantiation) + _s("") + _s("") + _ss([])
    return body


def _cache(path):
    body = struct.pack("<I", 2) + _s("Acme") + _s("1.2.3")
    body += _ss([k for k, _t in KEYS]) + struct.pack("<Q", len(KEYS)) + b"".join(struct.pack("<H", t) for _k, t in KEYS)
    body += _ss(ENUMS)
    # vendor map: one vendor with one printer model
    body += struct.pack("<Q", 1) + _s("Acme") + _s("Acme") + _s("Acme") + _s("1.2.3") + _s("") + _s("")
    body += struct.pack("<Q", 1) + _s("Acme One") + _s("Acme One") + _s("A1") + b"\x00" + _s("fam")
    body += struct.pack("<Q", 1) + _s("0.4") + _ss([]) + _ss([]) + b"".join(_s("") for _ in range(9))
    body += _ss([]) + _ss([])
    body += struct.pack("<I", 1) + _entry("0.20mm @Acme", [
        ("layer_height", struct.pack("<d", 0.2)), ("sparse_infill_density", struct.pack("<d", 15.0)),
        ("wall_generator", struct.pack("<IH", 1, 1))])
    body += struct.pack("<I", 2) + _entry("Acme PLA @base", [
        ("filament_type", _ss(["PLA"])), ("nozzle_temperature", struct.pack("<Qi", 1, 215)),
        ("filament_flow_ratio", struct.pack("<Qd", 1, 0.98))], instantiation="false")
    body += _entry("Acme PLA", [("compatible_printers", _ss(["Acme One 0.4 nozzle"])),
                                ("nozzle_temperature", struct.pack("<Qi", 1, 220))], inherits="Acme PLA @base")
    body += struct.pack("<I", 1) + _entry("Acme One 0.4 nozzle", [
        ("printer_model", _s("Acme One")),
        ("printable_area", struct.pack("<Q4d", 2, 0, 0, 200, 0)),
        ("retract_when_changing_layer", struct.pack("<Q", 1) + b"\x01")])
    body += struct.pack("<Q", 0)
    head = oc.MAGIC + struct.pack("<IQI", 2, len(body), zlib.crc32(body) & 0xFFFFFFFF)
    path.write_bytes(head + body)


def test_read_cache(tmp_path):
    p = tmp_path / "Acme.opc"
    _cache(p)
    c = oc.read_vendor_cache(str(p))
    assert (c.name, c.version) == ("Acme", "1.2.3")
    assert c.models[0]["name"] == "Acme One" and c.models[0]["nozzle_diameter"] == "0.4"
    proc = c.process[0]
    assert proc["layer_height"] == "0.2" and proc["sparse_infill_density"] == "15%"
    assert proc["wall_generator"] == "arachne"
    fil = {f["name"]: f for f in c.filament}
    assert fil["Acme PLA"]["inherits"] == "Acme PLA @base"
    assert fil["Acme PLA @base"]["filament_flow_ratio"] == ["0.98"]
    m = c.machine[0]
    assert m["printable_area"] == ["0x0", "200x0"]
    assert m["retract_when_changing_layer"] == ["1"]
    assert oc.peek(str(p)) == ("Acme", "1.2.3")


def test_install_uses_cache(tmp_path):
    (tmp_path / "system").mkdir()
    _cache(tmp_path / "system" / "Acme.opc")
    conf = {"app": {}, "presets": {"machine": "Acme One 0.4 nozzle", "process": "0.20mm @Acme",
                                   "filaments": ["Acme PLA"]}}
    (tmp_path / "OrcaSlicer.conf").write_text(json.dumps(conf))
    inst = BambuInstall(SLICER_ORCA, str(tmp_path))
    r = inst.resolve("filament", "Acme PLA")
    assert r["nozzle_temperature"] == ["220"] and r["filament_type"] == ["PLA"]
    assert inst.system_preset("machine", "Acme One 0.4 nozzle") is not None


def test_bad_crc(tmp_path):
    p = tmp_path / "Acme.opc"
    _cache(p)
    data = bytearray(p.read_bytes())
    data[-1] ^= 1
    p.write_bytes(bytes(data))
    try:
        oc.read_vendor_cache(str(p))
    except oc.CacheError:
        pass
    else:
        raise AssertionError("corrupt cache accepted")
