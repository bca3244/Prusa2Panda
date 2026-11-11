"""Generate bambu_schema.py, orca_schema.py and prusa_schema.py from slicer sources.

Adapted from Panda2Prusa's tools/gen_schema.py (MIT). Differences:

* emits Bambu Studio and OrcaSlicer separately (they diverged: different key names,
  enum values and defaults);
* parses ``set_default_value(...)`` so every module carries DEFAULTS serialized the
  way the slicer writes Metadata/project_settings.config (JSON): scalars as strings,
  vectors as lists of strings, nil as "nil";
* parses the ``s_keys_map_*`` enum tables, so OPTIONS lists the enum values the option
  definition offers, and ENUM_ACCEPTED lists every name the config parser accepts
  (keys map: may include legacy aliases);
* emits the variant (multi-extruder) key sets and PROJECT_KEYS.

Usage (sources already on disk; no download):

    python3 gen_schema.py --bambu-src <BambuStudio>/src/libslic3r \\
        --orca-src <OrcaSlicer>/src/libslic3r --prusa-src <dir with PrusaSlicer 2.9.7 files> \\
        --prusa-ref-schema <Panda2Prusa>/panda2prusa/prusa_schema.py --out .

The Prusa DEFAULTS come primarily from the reference prusa_schema.py (generated from
``prusa-slicer-console --save``, i.e. exact); keys it lacks are filled from source.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys

TYPE_CODES = {
    "coFloat": "float", "coFloats": "floats", "coInt": "int", "coInts": "ints",
    "coBool": "bool", "coBools": "bools", "coString": "string", "coStrings": "strings",
    "coPercent": "percent", "coPercents": "percents",
    "coFloatOrPercent": "floatorpercent", "coFloatsOrPercents": "floatsorpercents",
    "coEnum": "enum", "coEnums": "enums", "coPoint": "point", "coPoints": "points",
    "coPoint3": "point3", "coPointsGroups": "pointsgroups", "coIntsGroups": "intsgroups",
    "coNone": "none",
}
VECTOR_TYPES = {"floats", "ints", "bools", "strings", "percents", "floatsorpercents", "enums",
                "points", "pointsgroups", "intsgroups"}


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def strip_comments(src: str) -> str:
    """Remove // and /* */ comments, keeping string literals intact."""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"' or c == "'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return strip_if0("".join(out))


def strip_if0(src: str) -> str:
    """Drop `#if 0 ... #endif` regions (keeping an `#else` branch)."""
    out, depth, skipping, lines = [], 0, 0, src.split("\n")
    for ln in lines:
        t = ln.strip()
        if skipping:
            if t.startswith("#if"):
                depth += 1
            elif t.startswith("#endif"):
                if depth == 0:
                    skipping = 0
                else:
                    depth -= 1
            elif t.startswith("#else") and depth == 0:
                skipping = 0
            continue
        if re.match(r"#if\s+0\b", t):
            skipping, depth = 1, 0
            continue
        out.append(ln)
    return "\n".join(out)


def balanced(text: str, start: int) -> tuple:
    """text[start] is an opening bracket; return (inner, index after closing)."""
    open_c = text[start]
    close_c = {"(": ")", "{": "}", "[": "]", "<": ">"}[open_c]
    depth = 0
    k = start
    while k < len(text):
        c = text[k]
        if c == '"':
            k += 1
            while k < len(text) and text[k] != '"':
                k += 2 if text[k] == "\\" else 1
        elif c in "({[":
            depth += 1
        elif c in ")}]":
            depth -= 1
            if depth == 0:
                return text[start + 1:k], k + 1
        k += 1
    return text[start + 1:], len(text)


def split_top(s: str) -> list:
    """Split on top-level commas."""
    parts, depth, cur, k = [], 0, [], 0
    while k < len(s):
        c = s[k]
        if c == '"':
            j = k + 1
            while j < len(s) and s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            cur.append(s[k:j + 1])
            k = j + 1
            continue
        if c in "({[":
            depth += 1
        elif c in ")}]":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        k += 1
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


_C_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"', "'": "'", "r": "\r", "0": "\0"}


def c_string(s: str):
    """Concatenated C string literals (optionally inside L()/_L()/u8) -> python str, else None."""
    s = s.strip()
    m = re.fullmatch(r'(?:_?L|_u8L|L_CONTEXT)\s*\((.*)\)', s, re.S)
    if m:
        s = split_top(m.group(1))[0]
    lits = re.findall(r'(?:u8)?"((?:[^"\\]|\\.)*)"', s)
    rest = re.sub(r'(?:u8)?"((?:[^"\\]|\\.)*)"', "", s).strip()
    if not lits or rest not in ("", "std::string()"):
        return None
    out = []
    for lit in lits:
        out.append(re.sub(r"\\(.)", lambda m: _C_ESCAPES.get(m.group(1), m.group(1)), lit))
    return "".join(out)


def fmt_num(x: float) -> str:
    """C++ ostream default formatting (6 significant digits, like %g)."""
    if isinstance(x, bool):
        return "1" if x else "0"
    if isinstance(x, int):
        return str(x)
    if x == int(x) and abs(x) < 1e15:
        s = "%g" % x
        return s
    return "%g" % x


class Ctx:
    def __init__(self, src: str, enum_maps: dict):
        self.src = src
        self.enum_maps = enum_maps  # EnumType -> [(name, const)]
        # const (unqualified) -> list of (EnumType, name); alphabetical-last wins per type
        self.const_names = {}
        for et, pairs in enum_maps.items():
            per = {}
            for name, const in sorted(pairs):
                per[const.split("::")[-1]] = name
            for c, name in per.items():
                self.const_names.setdefault(c, {})[et] = name
        self.constants = {}
        self.ordinals = {}  # unqualified enumerator -> int value (from enum declarations)
        for m in re.finditer(r"\benum\s+(?:class\s+)?([A-Za-z_]\w*)\s*(?::\s*[\w:]+\s*)?\{([^{}]*)\}", src):
            n = -1
            for item in split_top(m.group(2)):
                nm = re.match(r"([A-Za-z_]\w*)\s*(?:=\s*(.+))?$", item.strip())
                if not nm:
                    continue
                if nm.group(2) is not None:
                    try:
                        n = int(nm.group(2).strip(), 0)
                    except ValueError:
                        n = self.ordinals.get(nm.group(2).strip().split("::")[-1], n + 1)
                else:
                    n += 1
                self.ordinals.setdefault(nm.group(1), n)
        for m in re.finditer(r"#define\s+([A-Z_][A-Z_0-9]*)\s+(-?[\d.]+|true|false)[fF]?\s*$", src, re.M):
            self.constants[m.group(1)] = {"true": "1", "false": "0"}.get(m.group(2), m.group(2))
        for m in re.finditer(r"(?:constexpr|const)\s+(?:static\s+)?(?:double|float|int|auto|size_t)\s+([A-Za-z_]\w*)\s*=\s*(-?[\d.]+)[fF]?\s*;", src):
            self.constants.setdefault(m.group(1), m.group(2))
        self.constants.update({"INT_MAX": "2147483647", "FLT_MAX": "3.40282e+38", "M_PI": "3.14159"})


def eval_num(s: str, ctx: Ctx):
    s = s.strip()
    s = re.sub(r"(?<=[\d.])[fF]\b", "", s)
    s = re.sub(r"\b(?:float|double|int|size_t)\s*\(", "(", s)
    s = re.sub(r"static_cast<\s*(?:float|double|int)\s*>", "", s)
    s = re.sub(r"\(\s*(?:int|float|double)\s*\)", "", s)
    s = re.sub(r"\b(?:[A-Za-z_]\w*::)+([A-Za-z_]\w*)", r"\1", s)
    s = re.sub(r"\b([A-Za-z_]\w*)\b", lambda m: str(ctx.ordinals[m.group(1)]) if m.group(1) in ctx.ordinals and m.group(1) not in ctx.constants else m.group(1), s)
    for name, val in ctx.constants.items():
        s = re.sub(r"\b" + name + r"\b", val, s)
    if s in ("true", "false"):
        return s == "true"
    if not re.fullmatch(r"[\d.eE+\-*/() ]+", s):
        return None
    try:
        v = eval(s, {"__builtins__": {}}, {})  # arithmetic only (checked above)
    except Exception:
        return None
    if isinstance(v, float) and re.fullmatch(r"-?\d+", s.strip()):
        v = int(v)
    return v


def eval_enum(s: str, ctx: Ctx, enum_type):
    s = s.strip()
    s = re.sub(r"^\(\s*int\s*\)\s*", "", s).strip()
    m = re.fullmatch(r"(?:int|static_cast<int>)?\s*\((.*)\)", s)
    if m:
        s = m.group(1).strip()
    c = s.split("::")[-1]
    if re.fullmatch(r"\d+", c) and enum_type in ctx.enum_maps:
        n = int(c)
        names = sorted(a for a, k in ctx.enum_maps[enum_type] if ctx.ordinals.get(k.split("::")[-1]) == n)
        return names[-1] if names else None
    cand = ctx.const_names.get(c)
    if not cand:
        return None
    if enum_type and enum_type in cand:
        return cand[enum_type]
    return next(iter(cand.values()))


def eval_elem(s: str, kind: str, ctx: Ctx, enum_type=None):
    """One scalar element -> serialized string, or None if unresolvable."""
    s = s.strip()
    while s.startswith("(") and s.endswith(")") and balanced(s, 0)[1] == len(s):
        s = s[1:-1].strip()
    if "nil_value()" in s:
        return "nil"
    if kind not in ("point", "points", "floatorpercent", "floatsorpercents") and s.startswith("{") and balanced(s, 0)[1] == len(s):
        inner = split_top(balanced(s, 0)[0])
        if len(inner) == 1:
            return eval_elem(inner[0], kind, ctx, enum_type)
    if kind in ("string", "strings"):
        if s in ("", "std::string()", "\"\""):
            return ""
        return c_string(s)
    if kind in ("enum", "enums"):
        return eval_enum(s, ctx, enum_type)
    if kind in ("bool", "bools"):
        s = ctx.constants.get(s, s)
        if s in ("true", "1"):
            return "1"
        if s in ("false", "0"):
            return "0"
        return None
    if kind in ("point", "points"):
        m = re.fullmatch(r"(?:Vec2d|Pointf|Vec2f)?\s*[({](.*)[)}]", s, re.S)
        if not m:
            return None
        xy = [eval_num(p, ctx) for p in split_top(m.group(1))]
        if len(xy) != 2 or None in xy:
            return None
        return f"{fmt_num(xy[0])}x{fmt_num(xy[1])}"
    if kind in ("floatorpercent", "floatsorpercents"):
        m = re.fullmatch(r"(?:FloatOrPercent)?\s*[({](.*)[)}]", s, re.S)
        if m:
            parts = split_top(m.group(1))
            v = eval_num(parts[0], ctx)
            if v is None:
                return None
            pct = len(parts) > 1 and parts[1].strip() == "true"
            return fmt_num(v) + ("%" if pct else "")
        v = eval_num(s, ctx)
        return None if v is None else fmt_num(v)
    v = eval_num(s, ctx)
    if v is None:
        return None
    if kind in ("int", "ints"):
        v = int(v)
    if kind in ("float", "floats", "percent", "percents"):
        v = float(v)
        out = fmt_num(v)
    else:
        out = fmt_num(v)
    if kind in ("percent", "percents"):
        out += "%"
    return out


def parse_default(expr: str, kind: str, ctx: Ctx, enum_type=None):
    """`new ConfigOptionX<...>(args)` / `{args}` -> serialized default or None."""
    m = re.match(r"\s*new\s+ConfigOption([A-Za-z0-9]+)\s*(<\s*([A-Za-z_:0-9]+)\s*>)?\s*", expr)
    if not m:
        return None
    if m.group(3):
        enum_type = m.group(3).split("::")[-1]
    rest = expr[m.end():]
    if not rest or rest[0] not in "({":
        return None
    inner, _ = balanced(rest, 0)
    inner = inner.strip()
    is_vec = kind in VECTOR_TYPES
    if not is_vec:
        if inner == "":
            return "" if kind == "string" else None
        parts = split_top(inner)
        if kind == "floatorpercent" and len(parts) == 2:
            v = eval_num(parts[0], ctx)
            return None if v is None else fmt_num(v) + ("%" if parts[1].strip() == "true" else "")
        if kind == "point" and len(parts) == 1:
            return eval_elem(parts[0], kind, ctx, enum_type)
        if kind == "point" and len(parts) == 2:
            return eval_elem("{" + inner + "}", kind, ctx, enum_type)
        if kind in ("int", "float", "percent") and len(parts) == 1 and "nil_value" in parts[0]:
            return "nil"
        return eval_elem(parts[0], kind, ctx, enum_type)
    # vectors
    if kind in ("pointsgroups", "intsgroups"):
        return []
    if inner == "":
        return []
    if rest[0] == "(":
        # ({...}) / (std::vector<T>{...}) / (n, value) / (single brace list)
        parts = split_top(inner)
        if len(parts) == 1:
            p = parts[0]
            p = re.sub(r"^std::vector<[^>]*>\s*", "", p)
            if p.startswith("{"):
                inner = balanced(p, 0)[0]
            else:
                return None
        elif len(parts) == 2 and kind != "points" and re.fullmatch(r"\d+", parts[0]):
            v = eval_elem(parts[1], kind, ctx, enum_type)
            return None if v is None else [v] * int(parts[0])
        elif kind == "points":
            pass  # (Vec2d(..), Vec2d(..))? treat as list
        else:
            return None
    elems = split_top(inner)
    out = []
    for e in elems:
        v = eval_elem(e, kind, ctx, enum_type)
        if v is None:
            return None
        out.append(v)
    return out


def parse_enum_maps(src: str) -> dict:
    out = {}
    for m in re.finditer(r"t_config_enum_values\s+s_keys_map_([A-Za-z0-9_]+)\s*(?:=\s*)?\{", src):
        inner, _ = balanced(src, m.end() - 1)
        pairs = re.findall(r'\{\s*"([^"]*)"\s*,\s*([A-Za-z_:0-9()]+?)\s*\}', inner)
        out[m.group(1)] = [(a, b.replace("int(", "").rstrip(")")) for a, b in pairs]
    return out


def parse_options(src: str, ctx: Ctx) -> dict:
    """{key: info} from PrintConfigDef bodies. First definition of a key wins."""
    pattern = r'this->add(_nullable)?\(\s*"([a-zA-Z_0-9]+)"\s*,\s*(co[A-Za-z0-9]+)\s*\)'
    adds = list(re.finditer(pattern, src))
    boundaries = sorted(set(
        [m.start() for m in re.finditer(r"this->add(?:_nullable)?\s*\(", src)]
        + [m.start() for m in re.finditer(r"\n}\s*\n", src)]
    ))
    import bisect
    table = {}
    var_of = {}
    for m in adds:
        nullable_call, key, ctype = m.group(1), m.group(2), m.group(3)
        if key in table:
            continue
        i = bisect.bisect_right(boundaries, m.start())
        end = boundaries[i] if i < len(boundaries) else min(len(src), m.end() + 6000)
        block = src[m.end():end]
        line_start = src.rfind("\n", 0, m.start())
        vm = re.search(r"auto\s*\*?\s*(def_[a-z_0-9]+)\s*=", src[line_start:m.start()])
        if vm:
            var_of[vm.group(1)] = key
        kind = TYPE_CODES.get(ctype, ctype)
        info = {
            "type": kind,
            "nullable": bool(nullable_call) or bool(re.search(r"def->nullable\s*=\s*true", block)),
            "block": block,
        }
        et = re.search(r"ConfigOptionEnum(?:s)?<\s*([A-Za-z_:0-9]+)\s*>\s*::get_enum_values", block)
        if not et:
            et = re.search(r"ConfigOptionEnums?<\s*([A-Za-z_:0-9]+)\s*>", block)
        if et:
            info["enum_type"] = et.group(1).split("::")[-1]
        enum = [e.group(1) for e in re.finditer(r'enum_values\.(?:push_back|emplace_back)\(\s*"([^"]*)"\s*\)', block)]
        if not enum:
            ev = re.search(r"enum_values\s*=\s*\{", block)
            if ev:
                enum = re.findall(r'"([^"]*)"', balanced(block, ev.end() - 1)[0])
        if not enum:
            ev = re.search(r"enum_values\s*=\s*(def_[a-z_0-9]+)->enum_values", block)
            if ev and ev.group(1) in var_of and var_of[ev.group(1)] in table:
                enum = list(table[var_of[ev.group(1)]].get("enum") or [])
        if not enum:
            se = re.search(r"set_enum(?:<[^>]*>)?\s*\(", block)
            if se:
                j = block.find("{", se.end())
                if j != -1:
                    inner = balanced(block, j)[0]
                    pairs = re.findall(r'\{\s*"([^"]*)"\s*,', inner)
                    enum = pairs if pairs else re.findall(r'"([^"]*)"', inner)
                sem = re.search(r"set_enum<\s*([A-Za-z_:0-9]+)\s*>", block)
                if sem:
                    info.setdefault("enum_type", sem.group(1).split("::")[-1])
                    if not enum:
                        enum = [a for a, _ in ctx.enum_maps.get(info["enum_type"], [])]
        if not enum and info.get("enum_type") in ctx.enum_maps:
            enum = [a for a, _ in ctx.enum_maps[info["enum_type"]]]
        if enum and kind in ("enum", "enums"):
            info["enum"] = enum
        for bound in ("min", "max"):
            found = re.findall(r"def->" + bound + r"\s*=\s*(-?\d+(?:\.\d*)?(?:e[+-]?\d+)?)\s*[f]?\s*;", block)
            if found:
                info[bound] = float(found[-1])
        ro = re.search(r'def->ratio_over\s*=\s*"([a-z_0-9]*)"', block)
        if ro:
            info["ratio_over"] = ro.group(1)
        if "enum_type" not in info:
            ek = re.search(r"enum_keys_map\s*=\s*&\s*s_keys_map_([A-Za-z0-9_]+)", block)
            if ek:
                info["enum_type"] = ek.group(1)
        dm = re.search(r"set_default_value\s*\(", block)
        if dm:
            expr, _ = balanced(block, dm.end() - 1)
            info["default_src"] = expr.strip()
            d = parse_default(expr, kind, ctx, info.get("enum_type"))
            if d is not None:
                info["default"] = d
        table[key] = info
    return table


def parse_classes(src: str) -> dict:
    out = {}
    for m in re.finditer(r"PRINT_CONFIG_CLASS_(?:DERIVED_)?DEFINE\s*\(\s*\n?\s*([A-Za-z_0-9]+)\s*,", src):
        start = m.end()
        end = re.search(r"\n\)", src[start:])
        body = src[start:start + end.start()] if end else src[start:start + 20000]
        out[m.group(1)] = re.findall(
            r"\(\(\s*ConfigOption[A-Za-z]+(?:<[^>]*>)?\s*,\s*([a-zA-Z_0-9]+)\s*\)\)", body
        )
    return out


def class_enum_types(src: str) -> dict:
    return {k: t.split("::")[-1] for t, k in re.findall(
        r"\(\(\s*ConfigOptionEnums?<\s*([A-Za-z_:0-9]+)\s*>\s*,\s*([a-zA-Z_0-9]+)\s*\)\)", src)}


def parse_list(src: str, name: str) -> list:
    m = re.search(re.escape(name) + r"\s*(?:=\s*)?\{", src)
    if not m:
        return []
    inner, _ = balanced(src, m.end() - 1)
    return re.findall(r'"([a-zA-Z_0-9]+)"', inner)


# -- loops the literal-key regex cannot see ------------------------------------------------

def add_axis_limits(src: str, opts: dict, ctx: Ctx, speed_name: str) -> None:
    m = re.search(r"std::vector<AxisDefault>\s+axes\s*\{", src)
    if not m:
        return
    inner, _ = balanced(src, m.end() - 1)
    nullable = "ConfigOptionFloatsNullable(axis.max_feedrate)" in src
    for row in re.findall(r"\{\s*(\"[xyze]\".*?\})\s*\}", inner, re.S):
        parts = split_top(row + "}")
        axis = c_string(parts[0])
        vals = [parse_default("new ConfigOptionFloats{" + balanced(p, 0)[0] + "}", "floats", ctx)
                for p in parts[1:4]]
        for name, v in zip((speed_name, "machine_max_acceleration_", "machine_max_jerk_"), vals):
            opts.setdefault(name + axis, {"type": "floats", "nullable": nullable, "min": 0.0, "default": v})


def add_override_keys(src: str, opts: dict, list_name: str, keep_nullable_from_base: bool) -> None:
    keys = parse_list(src, list_name)
    for k in keys:
        base = k[len("filament_"):]
        b = opts.get(base)
        if b is None or k in opts:
            continue
        info = {"type": b["type"], "nullable": True}
        for f in ("enum", "enum_type", "min", "max", "default", "ratio_over"):
            if f in b:
                info[f] = b[f]
        opts[k] = info


def prusa_loop_keys(src: str, opts: dict, ctx: Ctx) -> None:
    add_axis_limits(src, opts, ctx, "machine_max_feedrate_")
    m = re.search(r"for\s*\(\s*const\s+char\s*\*\s*opt_key\s*:\s*\{(.*?)\}\s*\)", src, re.S)
    if m:
        for base in re.findall(r'"([a-z_0-9]+)"', m.group(1)):
            b = opts.get(base)
            if b and "filament_" + base not in opts:
                info = {"type": b["type"], "nullable": True}
                for f in ("enum", "enum_type", "min", "max"):
                    if f in b:
                        info[f] = b[f]
                info["default"] = ["nil"]
                opts["filament_" + base] = info
    opts.setdefault("filament_seam_gap_distance", {"type": "floatsorpercents", "nullable": True, "default": ["nil"]})
    for k in ("wipe_tower_x", "wipe_tower_y", "wipe_tower_rotation_angle"):
        opts.setdefault(k, {"type": "floats", "nullable": False})


def orca_loop_keys(src: str, opts: dict, ctx: Ctx) -> None:
    for m in re.finditer(r"for\s*\(\s*const\s+char\s*\*\s*key\s*:\s*\{([^}]*)\}\s*\)\s*\{", src):
        body, _ = balanced(src, m.end() - 1)
        tm = re.search(r"this->add\(\s*key\s*,\s*(co[A-Za-z]+)\s*\)", body)
        if not tm:
            continue
        kind = TYPE_CODES[tm.group(1)]
        dm = re.search(r"set_default_value\s*\(", body)
        d = parse_default(balanced(body, dm.end() - 1)[0], kind, ctx) if dm else None
        for k in re.findall(r'"([a-z_0-9]+)"', m.group(1)):
            opts.setdefault(k, {"type": kind, "nullable": False, "default": d})
    lm = re.search(r"auto\s+add_belt_remap\s*=\s*\[[^\]]*\]\s*\(([^)]*)\)\s*\{", src)
    if lm:
        body, _ = balanced(src, lm.end() - 1)
        et = re.search(r"ConfigOptionEnum<\s*([A-Za-z_0-9]+)\s*>::get_enum_values", body).group(1)
        enum = re.findall(r'"([^"]*)"', balanced(body, body.find("{", body.find("enum_values")))[0])
        for cm in re.finditer(r'add_belt_remap\(\s*"([a-z_0-9]+)".*?,\s*([A-Za-z_:]+)\s*,\s*com[A-Za-z]+\s*\)\s*;', src, re.S):
            opts.setdefault(cm.group(1), {"type": "enum", "nullable": False, "enum": enum, "enum_type": et,
                                          "default": eval_enum(cm.group(2), ctx, et)})


# -- output -----------------------------------------------------------------------------------

def fmt_set(name: str, items) -> str:
    items = sorted(set(items))
    lines = [f"{name} = frozenset(("]
    row = "   "
    for it in items:
        piece = f' "{it}",'
        if len(row) + len(piece) > 96:
            lines.append(row)
            row = "   "
        row += piece
    lines.append(row)
    lines.append("))")
    return "\n".join(lines)


def emit(path: str, title: str, source: str, opts: dict, keysets: dict, enum_maps: dict,
         defaults: dict, extra_doc: str = "") -> None:
    lines = [
        f'"""{title} config schema (GENERATED by gen_schema.py; do not edit).',
        "",
        f"Source: {source}.",
        "OPTIONS maps key -> (type, nullable, enum values, min, max). Types: float(s), int(s),",
        "bool(s), string(s), percent(s), floatorpercent(s), enum(s), point(s), points, pointsgroups.",
        "A plural type is a vector (per extruder / per filament / per variant); min/max None = unbounded.",
        "Enum values in OPTIONS are the ones the option definition offers (UI list); ENUM_ACCEPTED has",
        "every name the config parser accepts for that option (the s_keys_map_* table).",
        "DEFAULTS: the definition's default, serialized as in project_settings.config (JSON):",
        "scalars as strings, vectors as lists of strings, \"nil\" for an unset nullable element.",
        extra_doc,
        '"""',
        "",
        "OPTIONS = {",
    ]
    for k in sorted(opts):
        o = opts[k]
        enum = tuple(o["enum"]) if o.get("enum") else None
        lines.append(f'    "{k}": ({o["type"]!r}, {o["nullable"]!r}, {enum!r}, {o.get("min")!r}, {o.get("max")!r}),')
    lines += ["}", "", "# Enum type (C++) of each enum option.", "ENUM_TYPE = {"]
    for k in sorted(opts):
        if opts[k].get("enum_type") and opts[k]["type"] in ("enum", "enums"):
            lines.append(f'    "{k}": {opts[k]["enum_type"]!r},')
    lines += ["}", "", "# Percent base: a \"NN%\" value of key is relative to RATIO_OVER[key] (def->ratio_over).",
              "RATIO_OVER = {"]
    for k in sorted(opts):
        if opts[k].get("ratio_over"):
            lines.append(f'    "{k}": {opts[k]["ratio_over"]!r},')
    lines += ["}", "", "# Every name the parser accepts, per C++ enum type (s_keys_map_<Type>).", "ENUM_MAPS = {"]
    for et in sorted(enum_maps):
        lines.append(f"    {et!r}: {tuple(a for a, _ in enum_maps[et])!r},")
    lines += ["}", "", "ENUM_ACCEPTED = {k: ENUM_MAPS[t] for k, t in ENUM_TYPE.items() if t in ENUM_MAPS}", ""]
    lines += ["DEFAULTS = {"]
    for k in sorted(defaults):
        lines.append(f'    "{k}": {defaults[k]!r},')
    lines += ["}", ""]
    for name, items in keysets.items():
        lines.append(fmt_set(name, items))
    lines.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


def build(src_dir: str, flavor: str):
    cpp_raw = read(os.path.join(src_dir, "PrintConfig.cpp"))
    cpp = strip_comments(cpp_raw)
    hpp = strip_comments(read(os.path.join(src_dir, "PrintConfig.hpp")))
    const_p = os.path.join(src_dir, "PrintConfigConstants.hpp")
    consts = strip_comments(read(const_p)) if os.path.exists(const_p) else ""
    preset = strip_comments(read(os.path.join(src_dir, "Preset.cpp")))
    bundle_p = os.path.join(src_dir, "PresetBundle.cpp")
    bundle = strip_comments(read(bundle_p)) if os.path.exists(bundle_p) else ""
    enum_maps = parse_enum_maps(cpp)
    ctx = Ctx(consts + "\n" + hpp + "\n" + cpp, enum_maps)
    opts = parse_options(cpp, ctx)
    cls_et = class_enum_types(hpp)
    for k, t in cls_et.items():
        if k in opts and "enum_type" not in opts[k]:
            opts[k]["enum_type"] = t
            if "enum" not in opts[k] and t in enum_maps:
                opts[k]["enum"] = [a for a, _ in enum_maps[t]]
    # retry enum defaults now that enum_type is known
    for k, o in opts.items():
        if "default" not in o and o.get("default_src"):
            d = parse_default(o["default_src"], o["type"], ctx, o.get("enum_type"))
            if d is not None:
                o["default"] = d
    if flavor == "prusa":
        prusa_loop_keys(cpp, opts, ctx)
    else:
        add_axis_limits(cpp, opts, ctx, "machine_max_speed_")
        add_override_keys(cpp, opts, "filament_extruder_override_keys", False)
        add_override_keys(cpp, opts, "filament_overhang_override_keys", True)
        if flavor == "bambu":
            for k in ("filament_retract_length_toolchange", "filament_retract_restart_extra_toolchange"):
                if k in opts:
                    opts[k]["default"] = ["nil"]
        if flavor == "orca":
            orca_loop_keys(cpp, opts, ctx)
    classes = parse_classes(hpp)
    keysets = {
        "PRINT_KEYS": parse_list(preset, "s_Preset_print_options"),
        "FILAMENT_KEYS": parse_list(preset, "s_Preset_filament_options"),
        "MACHINE_LIMITS_KEYS": parse_list(preset, "s_Preset_machine_limits_options"),
        "EXTRUDER_KEYS": parse_list(cpp, "m_extruder_option_keys"),
        "PROJECT_KEYS": parse_list(bundle, "s_project_options"),
        "OBJECT_KEYS": classes.get("PrintObjectConfig", []),
        "REGION_KEYS": classes.get("PrintRegionConfig", []),
    }
    keysets["PRINTER_KEYS"] = (parse_list(preset, "s_Preset_printer_options")
                               + keysets["MACHINE_LIMITS_KEYS"] + keysets["EXTRUDER_KEYS"])
    if flavor != "prusa":
        keysets["PRINT_VARIANT_KEYS"] = parse_list(cpp, "print_options_with_variant")
        keysets["FILAMENT_VARIANT_KEYS"] = parse_list(cpp, "filament_options_with_variant")
        keysets["PRINTER_VARIANT_KEYS"] = (parse_list(cpp, "printer_options_with_variant_1")
                                           + parse_list(cpp, "printer_options_with_variant_2"))
        keysets["FILAMENT_EXTRUDER_OVERRIDE_KEYS"] = parse_list(cpp, "filament_extruder_override_keys")
    return opts, keysets, enum_maps


def report(name, opts, keysets, defaults):
    allk = set().union(*[set(v) for v in keysets.values()])
    missing = sorted(k for k in allk if k not in opts)
    nodef = sorted(k for k in allk if k in opts and k not in defaults)
    print(f"{name}: {len(opts)} options, {len(defaults)} defaults; " +
          ", ".join(f"{k}={len(set(v))}" for k, v in keysets.items()))
    if missing:
        print(f"  {name}: keys in key lists without a definition: {missing}", file=sys.stderr)
    if nodef:
        print(f"  {name}: listed keys without a parsed default ({len(nodef)}): {nodef}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bambu-src", required=True)
    ap.add_argument("--orca-src", required=True)
    ap.add_argument("--prusa-src", required=True)
    ap.add_argument("--prusa-ref-schema", help="reference prusa_schema.py whose DEFAULTS came from --save")
    ap.add_argument("--bambu-ref", default="bambulab/BambuStudio@master")
    ap.add_argument("--orca-ref", default="OrcaSlicer/OrcaSlicer@main")
    ap.add_argument("--prusa-ref", default="prusa3d/PrusaSlicer@version_2.9.7")
    ap.add_argument("--out", default=".")
    args = ap.parse_args()

    for flavor, src, title, ref, fname in (
        ("bambu", args.bambu_src, "Bambu Studio", args.bambu_ref, "bambu_schema.py"),
        ("orca", args.orca_src, "OrcaSlicer", args.orca_ref, "orca_schema.py"),
    ):
        opts, keysets, enum_maps = build(src, flavor)
        defaults = {k: o["default"] for k, o in opts.items() if "default" in o}
        emit(os.path.join(args.out, fname), title, ref, opts, keysets, enum_maps, defaults)
        report(fname, opts, keysets, defaults)

    opts, keysets, enum_maps = build(args.prusa_src, "prusa")
    src_defaults = {k: o["default"] for k, o in opts.items() if "default" in o}
    ref_defaults = {}
    if args.prusa_ref_schema:
        ns = {}
        exec(compile(read(args.prusa_ref_schema), args.prusa_ref_schema, "exec"), ns)  # trusted: generated tables
        ref_defaults = ns.get("DEFAULTS", {})
    fff = set().union(*[set(v) for v in keysets.values()]) | set(ref_defaults)
    fff |= {"print_settings_id", "filament_settings_id", "printer_settings_id", "physical_printer_settings_id",
            "filament_vendor", "printer_vendor", "extruder", "wipe_tower_x", "wipe_tower_y",
            "wipe_tower_rotation_angle", "compatible_printers_condition_cummulative",
            "compatible_prints_condition_cummulative", "inherits_cummulative"}
    opts = {k: v for k, v in opts.items() if k in fff}
    # Prusa ini (--save) format: vectors are ';'-joined (strings quoted when needed). Convert the
    # source-derived ones the same way so the table is uniform with the reference.
    def to_ini(k, v):
        if isinstance(v, list):
            if opts.get(k, {}).get("type") == "strings":
                return ";".join('"' + x.replace('"', '\\"') + '"' if (";" in x or x == "" or '"' in x) else x for x in v)
            if opts.get(k, {}).get("type") == "points":
                return ",".join(v)
            return ",".join(v)
        return v.replace("\n", "\\n") if isinstance(v, str) else v
    merged = {k: v for k, v in ref_defaults.items() if k in fff}
    mism = []
    for k, v in src_defaults.items():
        if k not in fff:
            continue
        iv = to_ini(k, v)
        if k in merged:
            if merged[k] != iv:
                mism.append((k, merged[k], iv))
        else:
            merged[k] = iv
    emit(os.path.join(args.out, "prusa_schema.py"), "PrusaSlicer FFF", args.prusa_ref, opts, keysets, enum_maps,
         merged, extra_doc="PrusaSlicer DEFAULTS use the .ini / Slic3r_PE.config text format (vectors joined with ','\n"
                           "or ';'), not JSON lists: they come from `prusa-slicer-console --save` via the reference\n"
                           "schema; keys it lacked are filled from PrintConfig.cpp.")
    report("prusa_schema.py", opts, keysets, merged)
    print(f"prusa: source-vs-ini default mismatches: {len(mism)} (of {len(set(src_defaults) & set(ref_defaults))} compared)")
    for k, a, b in mism[:400]:
        print(f"   {k}: ini={a!r} src={b!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
