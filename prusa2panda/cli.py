"""Command-line interface: python -m prusa2panda ..."""

from __future__ import annotations

import argparse
import sys

from .convert import (
    BEDS_PLATES,
    BEDS_SPLIT,
    PRINTER_AUTO,
    SLICER_AUTO,
    SLICERS,
    convert_file,
    current_printer,
    describe,
    filament_label,
    installed_printers,
    suggest_filament_map,
    used_extruders,
)


def _cmd_info(args) -> int:
    project = describe(args.input)
    print(f"File:      {args.input}")
    print(f"Producer:  {project.producer or 'unknown'}")
    print(f"Objects:   {len(project.objects)}")
    print(f"Build items: {len(project.build_items)}")
    if len(project.bed_ids) > 1:
        print(f"Beds:      {', '.join(str(b) for b in project.bed_ids)}")
        for b in project.bed_ids:
            n = sum(1 for bi in project.build_items if project.bed_of_item(bi) == b)
            print(f"   bed {b}: {n} object(s)")
    used = used_extruders(project)
    if used:
        print(f"Extruders used: {', '.join(filament_label(project, e) for e in used)}")
    print(f"Painted triangles: {project.painted_triangles}")
    cfg = project.config
    if cfg:
        print("Settings:")
        print(f"   printer:  {cfg.get('printer_settings_id') or '?'}")
        print(f"   print:    {cfg.get('print_settings_id') or '?'}  "
              f"(layer {cfg.get('layer_height')} mm, {cfg.get('perimeters')} perimeters, "
              f"{cfg.get('fill_density')} {cfg.get('fill_pattern')} infill, "
              f"supports {'on' if cfg.get('support_material') == '1' else 'off'})")
        names = cfg.vector("filament_settings_id") or []
        for e in used:
            name = names[e - 1] if e - 1 < len(names) else "?"
            print(f"   extruder {filament_label(project, e)}: {name}")
        for bed, codes in sorted(project.custom_gcodes.items()):
            if codes.items:
                print(f"   bed {bed}: {len(codes.items)} colour change / pause entries")
        if project.layer_ranges:
            print(f"   layer height ranges on {len(project.layer_ranges)} object(s)")
    else:
        print("Settings:  none (no Metadata/Slic3r_PE.config)")
    return 0


def _cmd_list_printers(args) -> int:
    names = installed_printers(args.slicer, args.config_dir)
    if not names:
        print("No Bambu Studio / OrcaSlicer installation found (or no printers installed in it).",
              file=sys.stderr)
        return 1
    cur = current_printer(args.slicer, args.config_dir)
    for n in names:
        print(("* " if n == cur else "  ") + n)
    print("\n* = currently selected in the slicer (used by --printer auto)")
    return 0


def _parse_map(text: str) -> dict:
    """Parse '3=2,1=1' (also accepts '3:2' and '3->2') into {3: 2, 1: 1}."""
    mapping = {}
    for pair in text.replace("->", "=").replace(":", "=").replace(";", ",").split(","):
        pair = pair.strip()
        if not pair:
            continue
        try:
            old, new = pair.split("=")
            old_i, new_i = int(old), int(new)
        except ValueError:
            raise SystemExit(f"Bad filament mapping {pair!r}; expected e.g. 3=2,1=1")
        if old_i < 1 or new_i < 1:
            raise SystemExit(f"Bad filament mapping {pair!r}; numbers start at 1")
        mapping[old_i] = new_i
    return mapping


def _parse_beds(chunks) -> list | None:
    if not chunks:
        return None
    beds = []
    for chunk in chunks:
        for p in str(chunk).replace(",", " ").split():
            try:
                beds.append(int(p))
            except ValueError:
                raise SystemExit(f"Bad bed number {p!r}; expected e.g. --bed 1 or --bed 1,2")
    return beds


def _prompt_filament_map(project, beds=None) -> dict | None:
    """Show the file's extruders and ask how to map them. None = auto-compact."""
    used = used_extruders(project, beds)
    if used in ([], [1]):
        return None  # single colour on extruder 1: nothing to decide
    print(f"This file uses {len(used)} extruder(s):")
    for e in used:
        print(f"  extruder {filament_label(project, e)}")
    suggestion = suggest_filament_map(used)
    sug = ", ".join(f"{k}->{v}" for k, v in suggestion.items())
    try:
        resp = input(
            f"Map to Bambu/Orca filaments [{sug}] (Enter=accept, 'keep' for no change, "
            "or e.g. 3=2,1=1): "
        ).strip()
    except EOFError:
        return None
    if not resp:
        return suggestion
    if resp.lower() in ("keep", "k", "none"):
        return {}
    return _parse_map(resp)


def _cmd_convert(args) -> int:
    beds = _parse_beds(args.bed)

    filament_map = None
    if args.map:
        filament_map = _parse_map(args.map)
    elif args.keep_extruders:
        filament_map = {}
    elif sys.stdin.isatty() and sys.stdout.isatty():
        filament_map = _prompt_filament_map(describe(args.input), beds)

    result = convert_file(
        args.input,
        args.output,
        beds=beds,
        filament_map=filament_map,
        settings=not args.no_settings,
        prusa_settings=args.prusa_settings or args.no_base_presets,
        printer=args.printer,
        use_base_presets=not args.no_base_presets,
        keep_filament_colours=args.keep_filament_colours,
        config_dir=args.config_dir,
        slicer=args.slicer,
        bed_layout=BEDS_SPLIT if args.beds_as_files else BEDS_PLATES,
    )
    s = result.stats
    print(f"Converted: {args.input}")
    for out in result.outputs:
        print(f"       ->  {out}")
    print(f"Producer:  {result.producer or 'unknown'}")
    print(f"Target:    {result.slicer_name}")
    if len(result.beds_available) > 1:
        shown = ",".join(str(b) for b in beds) if beds else "all"
        layout = ""
        if len(result.outputs) > 1:
            layout = " (one file per bed)"
        elif s.plates_written > 1:
            layout = f" (on {s.plates_written} plates)"
        print(f"Beds:      available {result.beds_available}, converted {shown}{layout}")
    print(f"Objects written:      {s.objects_written}")
    print(f"Parts written:        {s.parts_written}")
    print(f"Build items written:  {s.build_items_written}")
    print(f"Painted triangles:    {s.painted_triangles}")
    if s.filament_map:
        remapped = ", ".join(f"{k}->{v}" for k, v in sorted(s.filament_map.items()))
        print(f"Filaments remapped:   {remapped}")
    if s.settings_written:
        print(f"Settings:             {result.target_description}")
        print(f"   printer preset:    {s.printer_name}")
        print(f"   process preset:    {s.print_name}")
        for i, name in enumerate(s.filament_names, start=1):
            print(f"   filament {i}:        {name}")
        if s.custom_gcodes_written:
            print(f"   colour changes/pauses carried over: {s.custom_gcodes_written}")
        if s.layer_ranges_written:
            print(f"   layer height ranges carried over:   {s.layer_ranges_written}")
        if s.text_parts:
            print(f"   editable text parts:                {s.text_parts}")
    else:
        print(f"Settings:             not converted ({result.target_description})")
    for w in s.warnings:
        print(f"note: {w}", file=sys.stderr)
    if s.objects_written == 0:
        print("WARNING: no objects were written - check the bed selection.", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="prusa2panda",
        description="Convert PrusaSlicer .3mf projects to Bambu Studio / OrcaSlicer .3mf projects.",
    )
    p.add_argument("--info", metavar="FILE", help="inspect a PrusaSlicer 3mf and exit")
    p.add_argument("--list-printers", action="store_true",
                   help="list the Bambu Studio / OrcaSlicer printer presets available for --printer, then exit")
    p.add_argument("input", nargs="?", help="input PrusaSlicer .3mf")
    p.add_argument("output", nargs="?", help="output Bambu Studio / OrcaSlicer .3mf")
    p.add_argument(
        "--bed", "--plate",
        dest="bed",
        action="append",
        help="only convert these PrusaSlicer bed number(s), e.g. --bed 1 or --bed 1,2 (default: all)",
    )
    p.add_argument(
        "--beds-as-files",
        action="store_true",
        help="write one output file per PrusaSlicer bed (output_bedN.3mf). "
             "Default: one file, each bed on its own plate",
    )
    p.add_argument(
        "--map",
        metavar="MAP",
        help="map PrusaSlicer extruders to Bambu/Orca filament slots, e.g. --map 3=2 or --map 1=2,3=1",
    )
    p.add_argument(
        "--keep-extruders",
        action="store_true",
        help="keep the original extruder numbers as filament slots (no compaction, no prompt)",
    )
    g = p.add_argument_group("settings translation")
    g.add_argument(
        "--slicer",
        default=SLICER_AUTO,
        choices=SLICERS,
        help="target slicer: 'bambu' (Bambu Studio), 'orca' (OrcaSlicer) or 'auto' (whichever is "
             "installed; Bambu Studio when both are; default)",
    )
    g.add_argument(
        "--printer",
        default=PRINTER_AUTO,
        metavar="WHICH",
        help="printer preset for the output: 'auto' (the printer currently selected in the "
             "slicer; default), an installed preset name (see --list-printers), an exported "
             ".json preset / Bambu or Orca .3mf project, or 'prusa' (keep the PrusaSlicer printer "
             "as a custom printer; only for PCs without Bambu Studio / OrcaSlicer, switch the "
             "printer after opening)",
    )
    g.add_argument(
        "--prusa-settings",
        action="store_true",
        help="translate the PrusaSlicer print and filament settings into project presets. "
             "Default: keep the process and filament presets selected in the slicer and only "
             "apply the PrusaSlicer extruder colours as filament colours",
    )
    g.add_argument(
        "--no-settings",
        action="store_true",
        help="geometry, filaments and painting only; do not write any slicer settings",
    )
    g.add_argument(
        "--no-base-presets",
        action="store_true",
        help="with --prusa-settings (implied): layer the PrusaSlicer settings on the slicer's "
             "defaults instead of on the presets currently selected in the slicer",
    )
    g.add_argument(
        "--keep-filament-colours", "--keep-filament-colors",
        dest="keep_filament_colours",
        action="store_true",
        help="keep the filament presets' colours instead of the PrusaSlicer extruder colours",
    )
    g.add_argument(
        "--config-dir",
        metavar="DIR",
        help="Bambu Studio / OrcaSlicer configuration folder, if it is not in the usual place "
             "(e.g. %%APPDATA%%\\BambuStudio). BAMBU_STUDIO_CONFIG_DIR / ORCA_SLICER_CONFIG_DIR "
             "do the same",
    )
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_printers:
        try:
            return _cmd_list_printers(args)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    if args.info:
        args.input = args.info
        try:
            return _cmd_info(args)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    if not args.input or not args.output:
        parser.print_help()
        return 1
    try:
        return _cmd_convert(args)
    except SystemExit:
        raise
    except Exception as exc:  # surface a clean message, not a traceback
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
