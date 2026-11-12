# Changelog

## 0.2.0

- Release builds for macOS (an `.app` in a `.dmg`, for Apple silicon and Intel) and Linux
  (x86_64, built on Ubuntu 22.04), next to the Windows exes. The macOS app is not signed: see
  *Download* in the README for how to open it the first time.
- Every build runs real conversions with the built command line tool and checks that the GUI
  starts.
- A release whose tag does not match the version in `pyproject.toml` / `prusa2panda/__init__.py`
  (or has no CHANGELOG section) fails before anything is built.

## 0.1.2

- PrusaSlicer colour changes (M600) now change colour in Bambu Studio / OrcaSlicer. Those
  slicers write no G-code for a plain colour change, so each new colour gets its own filament
  slot (a copy of the filament it replaces) and the colour change becomes a filament change
  to that slot. With the PrusaSlicer printer kept, its own colour change G-code runs instead;
  in a multi-filament print a colour change becomes a pause.

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
