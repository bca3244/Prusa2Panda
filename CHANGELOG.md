# Changelog

## 0.1.1

- Fix the Windows exe crashing on every conversion with settings: the settings schemas were
  loaded in a way PyInstaller could not see, so they were missing from the exe.
- The release build now runs real conversions with the built exe before publishing it.

## 0.1.0

First version: PrusaSlicer 2.9 `.3mf` → Bambu Studio 2.x / OrcaSlicer 2.x `.3mf`.

- Geometry, part types and per-part extruders, and instances.
- MMU, support, seam and fuzzy skin painting.
- Editable text, converted to a Bambu text part or an Orca emboss shape.
- Multi-bed projects, written as plates or as one file per bed.
- Custom G-code per layer, layer ranges, variable layer height, the wipe tower position, and the thumbnail.
- Settings: keep the slicer's selected presets, translate the PrusaSlicer print, filament and
  printer settings (`--prusa-settings`, `--printer prusa`), or write geometry only.
- Preset matching: user presets are anchored on their system parents, and a Generic filament is
  picked when the material differs.
- Reads OrcaSlicer 2.5's binary system preset caches (`system/<Vendor>.opc`).
- CLI (`prusa2panda`) and Tkinter GUI (`prusa2panda-gui`).
