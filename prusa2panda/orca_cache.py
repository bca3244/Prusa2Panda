"""Reader for OrcaSlicer's binary system preset caches (``system/<Vendor>.opc``).

OrcaSlicer 2.5 ships and installs each vendor's system presets as one binary file instead
of ``<Vendor>.json`` plus a ``<Vendor>/`` tree. The layout is OrcaSlicer's
``src/libslic3r/PresetCacheFormat.cpp`` (cache format version 2), written with cereal's
little-endian binary archive:

- header: magic ``ZCRO``, u32 format version, u64 body size, u32 CRC32 of the body;
- body: u32 format version, vendor name, vendor version, then a dictionary (option keys,
  their ConfigOptionType, enum value names), the vendor profile map, then the process,
  filament and machine entries. Each entry is a preset in source form: its own keys, the
  preset it inherits and the presets it includes, plus name, sub path, instantiation, etc.

``read_vendor_cache`` turns a file into JSON-shaped preset dicts (scalars as strings,
vectors as lists of strings), the same shape a vendor's JSON sub files have, so the rest
of the tool treats both forms alike.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

MAGIC = b"ZCRO"
FORMAT_VERSION = 2

# ConfigOptionType (libslic3r/Config.hpp)
VEC = 0x4000
CO_FLOAT, CO_INT, CO_STRING, CO_PERCENT, CO_FLOAT_OR_PERCENT = 1, 2, 3, 4, 5
CO_POINT, CO_POINT3, CO_BOOL, CO_ENUM = 6, 7, 8, 9
CO_POINTS_GROUPS, CO_INTS_GROUPS = 10 + VEC, 11 + VEC
ENUM_UNNAMED = 0
INT_NIL = 2147483647  # ConfigOptionIntsNullable::nil_value()
BOOL_NIL = 0xFF

_MAX_COUNT = 1 << 24  # sanity bound for any count in an untrusted file


class CacheError(ValueError):
    pass


@dataclass
class CachedVendor:
    name: str
    version: str
    models: List[dict] = field(default_factory=list)  # {"name", "model_id", "family", "nozzle_diameter"}
    process: List[dict] = field(default_factory=list)  # JSON-shaped presets
    filament: List[dict] = field(default_factory=list)
    machine: List[dict] = field(default_factory=list)


class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def take(self, n: int) -> bytes:
        if n < 0 or self.p + n > len(self.d):
            raise CacheError("truncated preset cache")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack("<" + fmt, self.take(size))

    def u8(self) -> int:
        return self.unpack("B")[0]

    def u16(self) -> int:
        return self.unpack("H")[0]

    def u32(self) -> int:
        return self.unpack("I")[0]

    def i32(self) -> int:
        return self.unpack("i")[0]

    def u64(self) -> int:
        return self.unpack("Q")[0]

    def f64(self) -> float:
        return self.unpack("d")[0]

    def count(self) -> int:
        n = self.u64()
        if n > _MAX_COUNT:
            raise CacheError("implausible count in preset cache")
        return n

    def string(self) -> str:
        return self.take(self.count()).decode("utf-8", "replace")

    def strings(self) -> List[str]:
        return [self.string() for _ in range(self.count())]


# -- value formatting (what the option's serialize() would write in a JSON profile) ---------

def _num(v: float) -> str:
    if math.isnan(v):
        return "nil"
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return f"{v:.15g}"


def _int(v: int) -> str:
    return "nil" if v == INT_NIL else str(v)


def _read_value(r: _Reader, typ: int, enums: List[str]):
    """One option's value: a string for a scalar, a list of strings for a vector."""
    if typ in (CO_ENUM, CO_ENUM | VEC):
        n = r.u32()
        if n > _MAX_COUNT:
            raise CacheError("implausible enum count in preset cache")
        vals = []
        for _ in range(n):
            idx = r.u16()
            if idx == ENUM_UNNAMED:
                vals.append(_int(r.i32()))
            elif idx < len(enums):
                vals.append(enums[idx])
            else:
                raise CacheError("enum index past the dictionary")
        return vals[0] if typ == CO_ENUM and vals else vals
    if typ == CO_FLOAT:
        return _num(r.f64())
    if typ == CO_PERCENT:
        return _num(r.f64()) + "%"
    if typ == CO_FLOAT_OR_PERCENT:
        v, pct = r.f64(), r.u8()
        return _num(v) + ("%" if pct else "")
    if typ == CO_INT:
        return str(r.i32())
    if typ == CO_BOOL:
        return "1" if r.u8() else "0"
    if typ == CO_STRING:
        return r.string()
    if typ == CO_POINT:
        x, y = r.f64(), r.f64()
        return f"{_num(x)}x{_num(y)}"
    if typ == CO_POINT3:
        x, y, z = r.f64(), r.f64(), r.f64()
        return f"{_num(x)}x{_num(y)}x{_num(z)}"
    if typ == CO_FLOAT | VEC:
        return [_num(r.f64()) for _ in range(r.count())]
    if typ == CO_PERCENT | VEC:
        return [(_num(v) if math.isnan(v) else _num(v) + "%") for v in (r.f64() for _ in range(r.count()))]
    if typ == CO_FLOAT_OR_PERCENT | VEC:
        out = []
        for _ in range(r.count()):
            v, pct = r.f64(), r.u8()
            out.append(_num(v) if math.isnan(v) else _num(v) + ("%" if pct else ""))
        return out
    if typ == CO_INT | VEC:
        return [_int(r.i32()) for _ in range(r.count())]
    if typ == CO_BOOL | VEC:
        out = []
        for b in r.take(r.count()):
            out.append("nil" if b == BOOL_NIL else ("1" if b else "0"))
        return out
    if typ == CO_STRING | VEC:
        return r.strings()
    if typ == CO_POINT | VEC:
        out = []
        for _ in range(r.count()):
            x, y = r.f64(), r.f64()
            out.append(f"{_num(x)}x{_num(y)}")
        return out
    if typ == CO_INTS_GROUPS:
        groups = []
        for _ in range(r.count()):
            groups.append(",".join(_int(r.i32()) for _ in range(r.count())))
        return groups
    if typ == CO_POINTS_GROUPS:
        groups = []
        for _ in range(r.count()):
            pts = []
            for _ in range(r.count()):
                x, y = r.f64(), r.f64()
                pts.append(f"{_num(x)}x{_num(y)}")
            groups.append(",".join(pts))
        return groups
    raise CacheError(f"unknown option type {typ} in preset cache")


def _read_config(r: _Reader, keys: List[str], types: List[int], enums: List[str]) -> dict:
    out = {}
    for _ in range(r.u32()):
        idx = r.u16()
        if idx >= len(keys):
            raise CacheError("option index past the dictionary")
        out[keys[idx]] = _read_value(r, types[idx], enums)
    return out


def _read_vendors(r: _Reader) -> Dict[str, List[dict]]:
    vendors: Dict[str, List[dict]] = {}
    for _ in range(r.count()):
        key = r.string()
        # VendorProfile: name, id, config_version (Semver as string), urls, models, sets
        r.string(); r.string(); r.string(); r.string(); r.string()  # noqa: E702
        models = []
        for _ in range(r.count()):
            m_id, m_name, m_model_id = r.string(), r.string(), r.string()
            r.u8()  # technology
            family = r.string()
            variants = [r.string() for _ in range(r.count())]
            r.strings()  # default_materials
            r.strings()  # not_support_bed_types
            for _ in range(9):  # bed_model .. hotend_model
                r.string()
            models.append({"id": m_id, "name": m_name, "model_id": m_model_id, "family": family,
                           "nozzle_diameter": ";".join(variants)})
        r.strings()  # default_filaments (std::set)
        r.strings()  # default_sla_materials
        vendors[key] = models
    return vendors


def _read_entries(r: _Reader, keys, types, enums) -> List[dict]:
    out = []
    n = r.u32()
    if n > _MAX_COUNT:
        raise CacheError("implausible preset count in preset cache")
    for _ in range(n):
        name, sub_path = r.string(), r.string()
        cfg = _read_config(r, keys, types, enums)
        inherits = r.string()
        includes = r.strings()
        description = r.string()
        instantiation = r.string()
        setting_id = r.string()
        filament_id = r.string()
        renamed_from = r.strings()
        cfg["name"] = name
        cfg["sub_path"] = sub_path
        if inherits:
            cfg["inherits"] = inherits
        if includes:
            cfg["include"] = includes
        if description:
            cfg["description"] = description
        cfg["instantiation"] = instantiation or "true"
        if setting_id:
            cfg["setting_id"] = setting_id
        if filament_id:
            cfg["filament_id"] = filament_id
        if renamed_from:
            cfg["renamed_from"] = renamed_from
        out.append(cfg)
    return out


def read_vendor_cache(path: str) -> CachedVendor:
    """Read one ``<Vendor>.opc``. Raises CacheError for a file this reader cannot use."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 20 or data[:4] != MAGIC:
        raise CacheError("not an OrcaSlicer preset cache")
    version, size, crc = struct.unpack_from("<IQI", data, 4)
    if version != FORMAT_VERSION:
        raise CacheError(f"unsupported preset cache version {version}")
    body = data[20:]
    if size != len(body):
        raise CacheError("preset cache size mismatch")
    if zlib.crc32(body) & 0xFFFFFFFF != crc:
        raise CacheError("preset cache CRC mismatch")
    r = _Reader(body)
    if r.u32() != FORMAT_VERSION:
        raise CacheError("unsupported preset cache body version")
    vendor, vendor_version = r.string(), r.string()
    keys = r.strings()
    types = [r.u16() for _ in range(r.count())]
    enums = r.strings()
    if len(keys) != len(types):
        raise CacheError("preset cache dictionary is inconsistent")
    vendors = _read_vendors(r)
    out = CachedVendor(vendor, vendor_version, models=vendors.get(vendor, []))
    out.process = _read_entries(r, keys, types, enums)
    out.filament = _read_entries(r, keys, types, enums)
    out.machine = _read_entries(r, keys, types, enums)
    return out


def peek(path: str) -> Optional[Tuple[str, str]]:
    """(vendor, version) of a cache, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(1044)
        if head[:4] != MAGIC:
            return None
        r = _Reader(head[20:])
        if r.u32() != FORMAT_VERSION:
            return None
        return r.string(), r.string()
    except (OSError, CacheError, struct.error):
        return None
