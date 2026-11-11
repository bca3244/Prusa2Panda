# Prusa2Panda

**PrusaSlicer → Bambu Studio / OrcaSlicer.**

Convert **PrusaSlicer** `.3mf` project files into **Bambu Studio** or **OrcaSlicer** `.3mf`
project files. The converted file opens on your own Bambu/Orca printer with your own
process and filament presets. Parts land on the right filaments in the right colours, and
painting and editable text stay intact. If you also want the PrusaSlicer print and filament
settings, one option brings them along as project presets.

It is the mirror of [Panda2Prusa](https://github.com/CupsOhJoe/Panda2Prusa): it has the same
switches and the same GUI, and it runs in the other direction.

## What carries over

**Geometry**

- **Objects, instances and transforms.** PrusaSlicer bakes each object into one merged mesh.
  Prusa2Panda splits that mesh back into one Bambu part per volume, using the `firstid`/`lastid`
  triangle ranges, and writes the part files through the 3MF production extension
  (`3D/Objects/object_N.model`).
- **Part types and per-part extruders.** Modifiers, negative volumes, and support
  enforcers/blockers keep their type. A part's extruder becomes its filament. A single-part
  object carries the extruder on the object, because that is where Bambu reads it.
- **Editable text.** A PrusaSlicer *Emboss* text volume becomes a Bambu Studio text part
  (`text_info`), or an OrcaSlicer emboss shape. The text, font, size, depth and style carry over.
- **Beds → plates.** Each PrusaSlicer bed (2.9 multi-bed, including the legacy pre-2.9 layout)
  becomes its own plate, laid out on the Bambu plate grid. `--beds-as-files` writes one file per
  bed instead.

**Painting**

- **MMU / multi-material painting.** The extruder states are remapped when filaments are
  compacted.
- **Support** enforcer/blocker painting, **seam** painting and **fuzzy skin** painting.

**Per-layer and per-object data**

- **Custom G-code per layer**: colour changes, pauses and custom G-code, per plate.
- **Layer ranges** (height range modifiers) and **variable layer height** profiles.
- **Per-object and per-part settings**, translated to their Bambu/Orca keys.
- **Wipe tower position**, per plate.
- **Thumbnail.**

**Settings**

Prusa2Panda reads your Bambu Studio / OrcaSlicer configuration (presets, current selection,
installed printers) and writes `project_settings.config` against it:

- **Default:** it keeps the printer, process and filament presets currently selected in the
  slicer, and applies the PrusaSlicer extruder colours as filament colours. If the material on
  a PrusaSlicer extruder differs from the selected filament (for example PETG in Prusa but PLA
  selected in Bambu), it uses a Generic preset of that material for the printer instead.
- **`--prusa-settings`:** it translates the PrusaSlicer print and filament settings (about
  300 keys, checked against generated schemas of all three slicers) and layers them on top of
  those presets, so only the real differences show as modified.
- **`--printer prusa`:** it keeps the PrusaSlicer printer as a custom printer, including bed
  shape, start/end G-code with translated placeholders, retraction, limits and the MMU purge
  volumes. Use this on a PC without Bambu Studio / OrcaSlicer, then switch the printer after
  opening the file.
- User presets are kept as they are. A user preset that inherits from a system preset is
  written as that system preset plus its own changes, the way the slicer stores it.

## Which printer does the output use?

| `--printer`           | Printer in the output                                             |
|-----------------------|-------------------------------------------------------------------|
| `auto` (default)      | The printer currently selected in Bambu Studio / OrcaSlicer       |
| a preset name         | That installed printer (see `--list-printers`)                    |
| a `.json` / `.3mf`    | The printer, process and filaments of that exported preset/project |
| `prusa`               | The PrusaSlicer printer, as a custom printer                      |

If no Bambu Studio / OrcaSlicer configuration is found, `auto` falls back to `prusa`.

## Install

You need Python 3.9 or newer.

```
pip install .
```

That installs the `prusa2panda` command line tool and the `prusa2panda-gui` launcher. The only
dependency is `lxml`. The GUI uses Tkinter, which ships with the python.org installers. On
Linux, install `python3-tk`.

## Using the GUI

Run `prusa2panda-gui` (or `python gui_launcher.py`).

1. Pick the PrusaSlicer `.3mf`. The output name defaults to `<name>_bambu.3mf`.
2. Choose the beds to convert, and whether each bed gets its own file.
3. Choose the target slicer: Auto, Bambu Studio or OrcaSlicer.
4. Choose how to handle the settings: keep your slicer presets, translate the PrusaSlicer
   settings, or write geometry only.
5. Choose the printer: Auto, an installed printer, *PrusaSlicer printer as-is*, or *Browse…*
   for an exported preset or project.
6. Click **Convert**. If the project uses several extruders, a mapping dialog shows each
   PrusaSlicer extruder with its colour swatch and lets you pick its filament slot.

## Command line

```
prusa2panda input.3mf output.3mf [options]
```

| Option | What it does |
|---|---|
| `--info FILE` | Inspect a PrusaSlicer 3mf (objects, beds, extruders, painting, text) and exit |
| `--list-printers` | List the printers available for `--printer`, then exit |
| `--bed N` / `--plate N` | Only convert these beds, e.g. `--bed 1` or `--bed 1,2` |
| `--beds-as-files` | One output file per bed (`output_bedN.3mf`) instead of one plate per bed |
| `--map 3=2,1=1` | Map PrusaSlicer extruders to filament slots |
| `--keep-extruders` | Keep the extruder numbers as filament slots (no compaction, no prompt) |
| `--slicer auto\|bambu\|orca` | Target slicer (default: whichever is installed, Bambu Studio first) |
| `--printer WHICH` | `auto`, a preset name, a `.json`/`.3mf` file, or `prusa` (see above) |
| `--prusa-settings` | Translate the PrusaSlicer print and filament settings into project presets |
| `--no-settings` | Geometry, filaments and painting only |
| `--no-base-presets` | With translation: layer the PrusaSlicer settings on the slicer defaults |
| `--keep-filament-colours` | Keep the filament presets' colours instead of the PrusaSlicer colours |
| `--config-dir DIR` | The Bambu/Orca configuration folder, if it is not in the usual place |

Without `--map` or `--keep-extruders`, the extruders the project uses are compacted to slots
1..K, so a model on extruders 3 and 5 prints from filaments 1 and 2. When run in a terminal,
the tool asks you to confirm the mapping.

The configuration folder is found in `%APPDATA%\BambuStudio` / `%APPDATA%\OrcaSlicer` on
Windows, `~/Library/Application Support/...` on macOS, and `~/.config/...` (or flatpak) on
Linux. The environment variables `BAMBU_STUDIO_CONFIG_DIR` / `ORCA_SLICER_CONFIG_DIR` override it.

## Building the exe yourself

```
pip install pyinstaller
pyinstaller --onefile --windowed --name Prusa2Panda gui_launcher.py
```

The result lands in `dist\Prusa2Panda.exe`.

## What does not carry over

- **Tool changers on Bambu Studio.** Bambu Studio cannot model a multi-tool printer. With
  `--printer prusa`, a PrusaSlicer printer with several tools becomes a one-extruder custom
  printer (OrcaSlicer keeps every tool). An MMU printer always becomes one extruder with
  several filaments.
- **Purge volumes on a Bambu printer.** The PrusaSlicer MMU purge volumes are far below what a
  Bambu printer flushes into its chute, so they are only used with `--printer prusa`.
- **Painting with more than 16 filaments.** The states above 16 are encoded differently by
  each slicer.
- **"Full spectrum" virtual extruders** and **cut-object connector links** (the cut parts
  themselves do carry over).
- **Settings with no equivalent.** These are listed as warnings after the conversion.

## Running the tests

```
pip install pytest
python -m pytest
```

The tests build small synthetic PrusaSlicer projects. To also convert your own projects, set
`PRUSA2PANDA_SAMPLES` to a folder of PrusaSlicer `.3mf` files.

## License

MIT
