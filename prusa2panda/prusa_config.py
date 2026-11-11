"""PrusaSlicer configuration model (the input side).

``PrusaConfig`` holds a flat ``key -> serialized value`` map in exactly the text form
PrusaSlicer uses in ``Metadata/Slic3r_PE.config`` (``; key = value`` lines) and in
``.ini`` presets (``key = value``). Vector values are kept serialized (``0.4,0.4`` /
``"Prusament PLA";PLA``) and split/joined on demand with the same escaping rules as
PrusaSlicer's ``escape_strings_cstyle``.
"""

from __future__ import annotations

import zipfile
from typing import Iterable, Optional

from . import prusa_schema as S

VECTOR_TYPES = frozenset(
    {"floats", "ints", "bools", "strings", "percents", "floatsorpercents", "enums", "points"}
)
SLIC3R_PE_CONFIG = "Metadata/Slic3r_PE.config"


def option_type(key: str) -> Optional[str]:
    info = S.OPTIONS.get(key)
    return info[0] if info else None


def is_vector(key: str) -> bool:
    return option_type(key) in VECTOR_TYPES


def is_nullable(key: str) -> bool:
    info = S.OPTIONS.get(key)
    return bool(info and info[1])


def enum_values(key: str) -> Optional[tuple]:
    info = S.OPTIONS.get(key)
    return info[2] if info else None


# -- string escaping (mirrors libslic3r/Config.cpp) -----------------------------

def escape_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")


def unescape_string(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            i += 1
            c = s[i]
            out.append("\r" if c == "r" else "\n" if c == "n" else c)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def escape_strings(values: Iterable[str]) -> str:
    values = list(values)
    parts = []
    for s in values:
        quote = (len(values) == 1 and s == "") or any(c in s for c in ' ;\t\\"\r\n')
        if quote:
            buf = ['"']
            for c in s:
                if c in '\\"':
                    buf.append("\\" + c)
                elif c == "\r":
                    buf.append("\\r")
                elif c == "\n":
                    buf.append("\\n")
                else:
                    buf.append(c)
            buf.append('"')
            parts.append("".join(buf))
        else:
            parts.append(s)
    return ";".join(parts)


def unescape_strings(text: str) -> list:
    out: list = []
    if text == "":
        return out
    i, n = 0, len(text)
    while True:
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            return out
        buf = []
        if text[i] == '"':
            i += 1
            while i < n and text[i] != '"':
                c = text[i]
                if c == "\\" and i + 1 < n:
                    i += 1
                    c = text[i]
                    c = "\r" if c == "r" else "\n" if c == "n" else c
                buf.append(c)
                i += 1
            i += 1  # closing quote
        else:
            while i < n and text[i] != ";":
                buf.append(text[i])
                i += 1
        out.append("".join(buf))
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            return out
        i += 1  # the ';'
        if i >= n:
            out.append("")
            return out


def split_vector(key: str, text: str) -> list:
    if text is None:
        return []
    if option_type(key) == "strings":
        return unescape_strings(text)
    if text == "":
        return []
    return text.split(",")


def join_vector(key: str, values: Iterable[str]) -> str:
    values = [str(v) for v in values]
    if option_type(key) == "strings":
        return escape_strings(values)
    return ",".join(values)


# -- the config -------------------------------------------------------------------

class PrusaConfig:
    """Ordered ``key -> serialized value`` map."""

    def __init__(self, data: Optional[dict] = None):
        self._d: dict = dict(data) if data else {}

    # basic mapping
    def __contains__(self, key: str) -> bool:
        return key in self._d

    def __len__(self) -> int:
        return len(self._d)

    def keys(self):
        return self._d.keys()

    def items(self):
        return self._d.items()

    def get(self, key: str, default=None):
        return self._d.get(key, default)

    def set(self, key: str, value) -> None:
        if isinstance(value, (list, tuple)):
            value = join_vector(key, value)
        elif isinstance(value, bool):
            value = "1" if value else "0"
        self._d[key] = str(value)

    def pop(self, key: str, default=None):
        return self._d.pop(key, default)

    def copy(self) -> "PrusaConfig":
        return PrusaConfig(self._d)

    def update_from(self, other: "PrusaConfig", keys: Optional[Iterable[str]] = None) -> None:
        """Copy values from ``other`` (all keys, or only ``keys`` that it has)."""
        if keys is None:
            self._d.update(other._d)
        else:
            for k in keys:
                if k in other._d:
                    self._d[k] = other._d[k]

    # vectors
    def vector(self, key: str) -> Optional[list]:
        if key not in self._d:
            return None
        return split_vector(key, self._d[key])

    def set_vector(self, key: str, values: Iterable[str]) -> None:
        self._d[key] = join_vector(key, values)

    def resize_vectors(self, n: int, keys: Iterable[str]) -> None:
        """Make the listed vector keys exactly ``n`` long (repeat the last value)."""
        for k in keys:
            if k not in self._d or not is_vector(k):
                continue
            vals = self.vector(k)
            if not vals:
                default = S.DEFAULTS.get(k, "")
                vals = split_vector(k, default) or [""]
            if len(vals) < n:
                vals = vals + [vals[-1]] * (n - len(vals))
            elif len(vals) > n:
                vals = vals[:n]
            self.set_vector(k, vals)

    # -- parsing / serialization --------------------------------------------------
    @classmethod
    def from_ini_text(cls, text: str) -> "PrusaConfig":
        """Flat ini (user preset / exported config). Section headers are ignored."""
        cfg = cls()
        for line in text.splitlines():
            line = line.strip("\r")
            if not line or line.lstrip().startswith(("#", ";", "[")):
                continue
            k, sep, v = line.partition("=")
            if not sep:
                continue
            cfg._d[k.strip()] = v.lstrip(" ").rstrip()
        return cfg

    @classmethod
    def from_slic3r_pe_text(cls, text: str) -> "PrusaConfig":
        """``; key = value`` lines as stored in Metadata/Slic3r_PE.config."""
        cfg = cls()
        for line in text.splitlines():
            line = line.strip("\r")
            if not line.startswith(";"):
                continue
            body = line[1:].strip()
            if not body or body.startswith("generated by"):
                continue
            k, sep, v = body.partition("=")
            if not sep:
                continue
            cfg._d[k.strip()] = v.lstrip(" ").rstrip()
        return cfg

    @classmethod
    def from_3mf(cls, path: str) -> Optional["PrusaConfig"]:
        with zipfile.ZipFile(path) as z:
            if SLIC3R_PE_CONFIG not in z.namelist():
                return None
            return cls.from_slic3r_pe_text(z.read(SLIC3R_PE_CONFIG).decode("utf-8", "replace"))

    @classmethod
    def from_file(cls, path: str) -> "PrusaConfig":
        """Load a PrusaSlicer ``.ini`` (exported config / preset) or a PrusaSlicer ``.3mf``."""
        if path.lower().endswith(".3mf"):
            cfg = cls.from_3mf(path)
            if cfg is None:
                raise ValueError(f"{path!r} has no {SLIC3R_PE_CONFIG}; not a PrusaSlicer project")
            return cfg
        with open(path, encoding="utf-8", errors="replace") as f:
            return cls.from_ini_text(f.read())

    def to_slic3r_pe_text(self, generator: str = "prusa2panda") -> str:
        lines = [f"; generated by {generator}", ""]
        for k in sorted(self._d):
            lines.append(f"; {k} = {self._d[k]}")
        return "\n".join(lines) + "\n"

    def to_ini_text(self) -> str:
        return "".join(f"{k} = {self._d[k]}\n" for k in sorted(self._d))
