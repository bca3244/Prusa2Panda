# Bambu Studio / OrcaSlicer presets: spec for a PrusaSlicer → Bambu/Orca 3MF converter

Sources: `BS` = BambuStudio master 930fe93 (SLIC3R_VERSION 02.09.01.70), `OS` = OrcaSlicer main 478cc90
(2.6.0-dev, SLIC3R_VERSION 02.08.01.55). Paths are relative to `src/` unless they say otherwise. `ref` = Panda2Prusa.

---------------------------------------------------------------------------------------------------
## 0. TL;DR / gotchas

1. **Clean selection mechanism.** `load_external_preset` compares the incoming config with the installed preset,
   ignoring a fixed set of UI keys (BS `libslic3r/Preset.cpp:2262-2276`, OS `Preset.cpp:3009-3022`). Before that
   comparison, **every key NOT listed in `different_settings_to_system[slot]` is overwritten with the value from
   the base preset**. The base is `inherits_group[slot]` if that is set and found, otherwise the preset named by
   `*_settings_id` if it is a system preset (BS `Preset.cpp:2326-2337`, OS `Preset.cpp:1107-1115` and `3050-3066`).
   ⇒ **A *partial* project_settings.config works.** It needs the right ids, `inherits_group`, and an empty or exact
   `different_settings_to_system`. You do not need any preset values for system presets.
2. `filament_colour` is **not** a filament-preset key. It is commented out in `s_Preset_filament_options`
   (BS `Preset.cpp:1144`, OS `Preset.cpp:1524`) and is a project option (`s_project_options` BS
   `PresetBundle.cpp:59-95`, OS `PresetBundle.cpp:125ff`). Changing colours never marks a filament "(modified)".
3. **You must emit `filament_diameter` with N entries.** Plater runs `Preset::normalize(config)` on (defaults + file)
   before loading (BS `Plater.cpp:12019`, OS `Plater.cpp:9738`). normalize resizes `filament_settings_id` to
   `len(filament_diameter)` (BS `Preset.cpp:472-497`), and the default length is 1. `validate_presets` also counts
   filaments by `filament_diameter` (BS `PresetBundle.cpp:1387`, OS `PresetBundle.cpp:3162`). The filament count
   itself comes from `len(filament_colour)`, and 0 throws "Invalid configuration file" (BS
   `PresetBundle.cpp:5286-5290`).
4. **Bambu Studio throws on inconsistent variants:** `len(filament_extruder_variant)` must equal
   `len(filament_self_index)` and be ≥ N (BS `PresetBundle.cpp:5332-5345`). The defaults are 1-element, so for N>1
   you **must emit both**. Orca auto-fills them (OS `PresetBundle.cpp:6247-6266`).
5. **All JSON values must be strings or arrays of strings.** A number or bool value is logged as "invalid json type"
   and dropped (BS `Config.cpp:1082-1085`).
6. **Orca's app config keeps per-printer selections in `orca_presets[]`.** `presets` holds only `machine`; process,
   filaments and colours live in the `orca_presets` entry whose `"machine"` equals that printer (OS
   `PresetBundle.cpp:4083-4130`, `4257-4345`).
7. On Windows `BambuStudio.conf` / `OrcaSlicer.conf` end with a `# MD5 checksum <32 hex>` line after the JSON (BS
   `AppConfig.cpp:561-578`, `972`). Parse only the leading JSON value (`json.JSONDecoder().raw_decode`).
8. **A user printer or filament preset whose `inherits_group` slot is not a system preset triggers a dialog.** It is
   a dismissable "Customized Preset" warning, unless `app.no_warn_when_modified_gcodes == "true"`. A gcode key in a
   printer or filament `different_settings_to_system` slot triggers the "Modified G-codes" dialog instead (BS
   `PresetBundle.cpp:1371-1446`, `Plater.cpp:12089-12122`).
9. Bambu `app.curr_bed_type` is an int enum, and the enums differ: BS SuperTack=5; OS Textured Cool=5, SuperTack=6
   (BS `PrintConfig.hpp:303`, OS `PrintConfig.hpp:509`). In project files it is always the string name.
10. **Orca 2.6-dev can ship `system/<Vendor>.opc` binary caches *instead of* JSON.** utils.cpp says "Packaging
    prunes the profile of every vendor that ships a cache" (OS `utils.cpp:2050-2075`, `PresetBundle.cpp:443-452`).
    A Python tool cannot read those, so stay on the name-only path (point 1).

---------------------------------------------------------------------------------------------------
## 1. Locations and file formats

### 1.1 Data directory (`data_dir`)

| App | Windows | macOS | Linux | Notes |
|---|---|---|---|---|
| Bambu Studio | `%APPDATA%\BambuStudio` | `~/Library/Application Support/BambuStudio` | `$XDG_CONFIG_HOME/BambuStudio`, else `~/.config/BambuStudio` | BS `GUI/GUI_App.cpp:2641-2672`. Internal/beta builds (`BBL_INTERNAL_TESTING`) append `Internal` / `Beta` (`:2662-2664`), so also probe `BambuStudioBeta`, `BambuStudioInternal`. Flatpak: `~/.var/app/com.bambulab.BambuStudio/config/BambuStudio`. No portable mode. |
| OrcaSlicer | `<exe dir>\data_dir` if it exists (portable), else `%APPDATA%\OrcaSlicer` | `<dir containing OrcaSlicer.app>/data_dir` if it exists, else `~/Library/Application Support/OrcaSlicer` | `<program dir>/data_dir` if it exists, else `$XDG_CONFIG_HOME/OrcaSlicer` or `~/.config/OrcaSlicer` | OS `GUI_App.cpp:2736-2775`, `libslic3r/utils.cpp:380-412`. Flatpak: `~/.var/app/com.orcaslicer.OrcaSlicer/config/OrcaSlicer` (legacy `io.github.softfever.OrcaSlicer`, migrated by OS `GUI_App.cpp:593-620`). |

Both apps accept the CLI `--datadir <dir>` (BS `PrintConfig.cpp:11311`, OS `:13142`). There is no dedicated
environment variable; offer `BAMBU_STUDIO_CONFIG_DIR` / `ORCA_SLICER_CONFIG_DIR` in the converter, like the ref
tool's `PRUSA_SLICER_CONFIG_DIR` (`ref prusa_config.py:319-332`). Detect a directory by the presence of
`BambuStudio.conf` / `OrcaSlicer.conf`.

```
<data_dir>/
  BambuStudio.conf | OrcaSlicer.conf     app config (JSON)    BS AppConfig.cpp:1517-1526, OS :1956-1965
  system/<Vendor>.json                   vendor index (BBL.json, Prusa.json, OrcaFilamentLibrary.json …)
  system/<Vendor>/{machine,process,filament}/*.json    system presets (copied from <install>/resources/profiles, OTA-updated)
  system/<Vendor>.opc                    Orca 2.6-dev binary cache (may replace JSON)
  user/<preset_folder>/{machine,process,filament}/<Preset Name>.json + .info
  user/<preset_folder>/{machine,process,filament}/base/*.json   user "custom root" presets, loaded first (BS Preset.cpp:1424-1427)
  user/<preset_folder>/_local/<bundle>/ and _subscribed/<bundle>/  (Orca only: bundle_metadata.json + machine|process|filament/)  OS PresetBundle.cpp:1467-1515
```

`preset_folder` = `app.preset_folder`. It is the cloud user id while logged in, `""` → `default` when logged out
(BS `PresetBundle.cpp:572-578`, `GUI_App.cpp:5940-5944`; OS `PresetBundle.cpp:925-930`, `GUI_App.cpp:6070-6076`).
Only that one folder is loaded.

### 1.2 App config (`*.conf`, JSON)

Parser: BS `AppConfig.cpp:609-835`, writer `:838-1000`; OS `AppConfig.cpp:900-1060`, writer `:1094-1220`.

**Bambu Studio**
```jsonc
{
  "header": "BambuStudio 02.03.00.70 ...",
  "app": { "preset_folder": "1234567890", "curr_bed_type": "4", "version": "02.03.00.70",
           "no_warn_when_modified_gcodes": "false", "import_project_action": "...",
           "filament_presets": [...], "filament_colors": [...] },   // app.* arrays are separate state, NOT the selection
  "presets": {
    "machine":  "Bambu Lab X1 Carbon 0.4 nozzle",
    "process":  "0.20mm Standard @BBL X1C",
    "filaments": ["Bambu PLA Basic @BBL X1C", "Bambu PETG HF @BBL X1C"],   // [0]→"filament", [i]→"filament_%02u"
    "filament_colors": "#00AE42,#FFFFFF",        // comma-joined STRING
    "filament_multi_colors": "...", "filament_color_types": "1,1",
    "extruder_ams_count": "...", "flush_volumes_matrix": "0.000000|280.000000|...", "flush_volumes_vector": "..."
  },
  "filaments": ["Bambu PLA Basic @BBL X1C", ...],     // names of visible (installed) system filaments
  "models": [ {"vendor":"BBL","model":"Bambu Lab X1 Carbon","nozzle_diameter":"0.4;0.2"}, ... ],  // installed printers
  "calis": [...]
}
```
Selections are loaded by BS `PresetBundle.cpp:2157-2202` and written by `:2397-2410` (`filament_%02u` keys:
`AppConfig.cpp:713-727`, `913-930`). `models` is parsed at `AppConfig.cpp:700-707`; `vendor` is the vendor JSON
basename (`VendorProfile(vendor_name)`, `PresetBundle.cpp:6193`).

**OrcaSlicer**
```jsonc
{
  "presets": { "machine": "Bambu Lab X1 Carbon 0.4 nozzle", "filaments": [] },
  "orca_presets": [
    { "machine": "Bambu Lab X1 Carbon 0.4 nozzle", "process": "0.20mm Standard @BBL X1C",
      "filament": "Bambu PLA Basic @BBL X1C", "filament_01": "Bambu PETG HF @BBL X1C",
      "filament_colors": "#26A69A,#FFFFFF", "curr_bed_type": "4", "extruder_ams_count": "...", "flush_volumes_matrix": "..." }
  ],
  "filaments": [...], "models": [...], "app": { "preset_folder": "...", ... }
}
```
- `orca_presets` load/save: OS `AppConfig.cpp:979-985`, `1210-1213`; accessors `AppConfig.hpp:274-291`; selection
  use `PresetBundle.cpp:4083-4130`.
- Fallback for older Orca builds: read `presets.process` / `presets.filaments`, which the `presets` parser still
  accepts (`AppConfig.cpp:926-940`).

### 1.3 Preset JSON format

| key | system preset | user preset (BS `Preset::save` `Preset.cpp:662-735`, `Config.cpp:1583-1626`) |
|---|---|---|
| `type` | `machine_model` / `machine` / `process` / `filament` | absent (infer the type from the folder) |
| `name` | preset name | preset name (the **file name** is authoritative: `Preset.cpp:1455`) |
| `inherits` | parent name, resolved only against earlier entries of the vendor list (`PresetBundle.cpp:6390-6411`) | the **system** parent (or empty for a root preset) |
| `from` | `system` | `User` (`Project` / `project` for embedded presets) |
| `setting_id` | e.g. `GM001`, `GP004`, `GFSA00` | lives in `.info` |
| `filament_id` | e.g. `GFA00` (inherited from the parent if absent: `:6396-6402`) | optional |
| `instantiation` | `"true"` = selectable; `"false"` = abstract base (`fdm_*_common`, `@base`, templates) (`:6468-6484`) | `instantiation:"false"` hides it (`Preset.cpp:1505`) |
| `include` | list of abstract configs whose non-default keys are applied after `inherits`, before own keys (`:6420-6459`) | – |
| `version` | vendor version | must parse as a semver and major ≤ app major, else skipped (`Preset.cpp:1491-1499`) |
| values | strings / arrays of strings; `"nil"` entries in variant vectors = "take from parent" | **diff only** against the parent, plus `*_settings_id`, `inherits`, and always the variant/id keys (`Preset.cpp:686-721`; "nil" via `set_with_nil`) |

- `.info` sidecar (INI): `sync_info`, `user_id`, `setting_id`, `base_id`, `updated_time` (BS `Preset.cpp:570-646`).
- **Resolving a system preset:** `default config` → `inherits` chain (recursive) → apply the non-default diff of
  each `include` item → own keys (BS `PresetBundle.cpp:6408-6467`; OS `PresetBundle.cpp:7840-7864`, `8497-8506`).
  Orca vendor filaments may also inherit from `OrcaFilamentLibrary` (OS `PresetBundle.cpp:3375-3400`).
- **Resolving a user preset:** the resolved parent with the user keys applied; `"nil"` → the parent value at the same
  index (BS `Preset.cpp:1508-1538`). A user preset without `inherits` = defaults + its own keys.
- **Printer presets need `printer_model` and `printer_variant`.** The `printer_model` must be a model listed in the
  vendor index, or the preset is ignored (BS `PresetBundle.cpp:6505-6528`).

### 1.4 "Installed / visible" (for `--list-printers`)

- **System printer.** Visible iff `models[]` holds `{vendor: <vendor json basename>, model: printer_model}` and its
  `nozzle_diameter` list (split on `;`) contains `printer_variant` (BS `Preset.cpp:873-886`, OS `:998-1009`).
  Only `instantiation:"true"` machines count.
- **User printers.** Every `user/<folder>/machine/*.json` (plus Orca `_local` / `_subscribed` bundles).
- **Filaments.** System filaments are visible iff listed in top-level `filaments[]` (BS `Preset.cpp:887-898`). User
  filaments are always visible. Hidden presets still load from a project (`it->is_visible = true`,
  `Preset.cpp:2365`).
- **`--list-printers` output:** user printers, then visible system printers; mark the current one
  (`presets.machine`).

---------------------------------------------------------------------------------------------------
## 2. How project_settings.config is applied on load

**Load sequence.** The project config is `Metadata/project_settings.config`, a JSON object written with
`"name":"project_settings","from":"project","version":"<SLIC3R_VERSION>"` (BS `bbs_3mf.cpp:173`, `8489-8497`). The
Plater (BS `Plater.cpp:11725-12160`, OS `Plater.cpp:9300-9850`) then:

1. **Vendor gate (Bambu Studio only).** If `printer_model` is not a BBL vendor model, `check_project_config` runs: it
   fails only when `len(nozzle_diameter) > 1` and `len(extruder_type) != len(nozzle_diameter)`. A failure means
   "invalid config, load geometry only" (BS `Plater.cpp:353-387`, `11796-11801`).
2. **File type and version.** The file type comes from the `Application` metadata (`BambuStudio-…` / `OrcaSlicer-…`,
   BS `bbs_3mf.cpp:4575-4580`, OS `:4176-4193`). If the file's major version is greater than the app's, only
   geometry is loaded or a "newer version" dialog appears (BS `Plater.cpp:11813-11893`). **Write a version ≤ the
   installed app version**, ideally the one read from `app.version`. Prusa files are treated as geometry only.
3. **Embedded presets.** `Metadata/{process,filament,machine}_settings_N.config` are loaded as project presets (BS
   `Plater.cpp:11952-11959`, `bbs_3mf.hpp:32`, `bbs_3mf.cpp:3110-3200`).
4. **Defaults merge and normalize.** `config = FullPrintConfig::defaults() + loaded` (BS `Plater.cpp:11985-11987`,
   OS `:9680`), then `Preset::normalize` (gotcha 3).
5. **Validation dialogs.** `validate_presets` (gotcha 8): BS `Plater.cpp:12091`, OS `:9798`.
6. **Load the presets.** `load_config_model` → `load_config_file_config(path, is_external=true, …)` (BS
   `PresetBundle.hpp:458`, `PresetBundle.cpp:5265`; OS `:6183`).

### 2.1 Per-slot vectors (slot order: `[print, filament1..N, printer]`, length N+2)
`inherits_group`, `different_settings_to_system` (each element = `;`-joined key list, `escape_strings_cstyle`),
`compatible_machine_expression_group` (N+2), `compatible_process_expression_group` (N), plus `filament_ids` (N),
`print_compatible_printers` (BS `PresetBundle.cpp:5296-5310`). Bambu itself writes them in `full_fff_config`
(BS `PresetBundle.cpp:4797-5070`). Its own semantics:
- `inherits_group[slot]` = the selected preset's `inherits` (`""` for a system preset).
- `different_settings_to_system[slot]` = keys that differ from the parent preset (`""` if clean).

`ignore_settings_list = {inherits, print_settings_id, filament_settings_id, printer_settings_id}` is always added
to the diff set (BS `:4758-4761`, OS `:5545-5557`).

### 2.2 `load_external_preset` decision table (BS `Preset.cpp:2282-2510`, OS `Preset.cpp:3028-3240`)
`original_name` = `print_settings_id` / `printer_settings_id` / `filament_settings_id[i]`.
`cfg` = defaults of the type + the project values, then non-diff keys ← base (gotcha 1).

| case | result |
|---|---|
| `original_name` found and `cfg == preset` (ignoring `compatible_*`, `inherits`, `*_settings_id`, `printer_model/variant`, `default_*_profile`, BS also `printer_nozzle_diameter_mode`) | **existing preset selected, clean** |
| not found, `inherits` found and `cfg == inherits preset` | the inherits preset is selected clean |
| found (or inherits found) and differs; slot is print, printer or filament 1 (`LoadAndSelect::Always`) | the preset is selected and **shown "(modified)"** (edited copy, `:2397-2420`) |
| found and differs; filament 2..N (`OnlyIfModified` / `Never`) | a new project preset `"<original_name>(<file>.3mf)"` |
| not found, no usable inherits | a new project preset `"<original_name>(<file>.3mf)"` (or `"<inherits>(<file>)"`); `is_project_embedded=true` (`:2441-2507`); full config = defaults + file values |

Notes on the other keys:
- **`printer_settings_id` naming a missing preset:** the last row applies, with a "Customized Preset" warning. Any
  printer works this way in Bambu Studio too: non-BBL printers load as third-party (only the vendor gate in step 1
  applies). An existing but not-installed system preset is found and made visible.
- **Ignored keys:** `print_compatible_printers` / `compatible_printers` and conditions are UI-only (ignored in the
  comparison).
- **`version` / `from` / `name`** in project_settings are header metadata. `"name":"project_settings"` only enables
  legacy migrations (`Config.cpp:1153ff`; avoid `support_type: hybrid(auto)`, legacy `wall_infill_order`,
  `reduce_infill_retraction`).

### 2.3 What makes the user's presets load *unmodified*

| selected preset is | `inherits_group[slot]` | `different_settings_to_system[slot]` | values needed |
|---|---|---|---|
| system | `""` (or its own name) | `""` | none |
| user, has system parent P | `P` | `;`-join of the keys in the user JSON (minus meta keys `name/from/version/inherits/*_settings_id`, plus `*_extruder_id`/`*_extruder_variant`) | those keys' **resolved** values (parent + user, "nil" resolved) |
| user, no system parent (custom root) **or** values unresolvable | **its own name** ("self-anchor") | `""` | none (`cfg` := that preset exactly); costs a "Customized Preset" warning for printer/filament slots (not for print) |

A partial config is fine. Missing keys come from `FullPrintConfig::defaults()`, then non-diff keys come from the
base. Missing project options (`s_project_options`) are reset to defaults (BS `PresetBundle.cpp:5559-5574`).

---------------------------------------------------------------------------------------------------
## 3. Multi-filament keys

### 3.1 Minimal project_settings.config (default mode, system presets, X1C, 2 filaments)
```json
{
  "name": "project_settings", "from": "project", "version": "02.03.00.70",
  "printer_technology": "FFF",
  "printer_settings_id": "Bambu Lab X1 Carbon 0.4 nozzle",
  "print_settings_id":   "0.20mm Standard @BBL X1C",
  "filament_settings_id": ["Bambu PLA Basic @BBL X1C", "Bambu PLA Basic @BBL X1C"],
  "inherits_group":               ["", "", "", ""],
  "different_settings_to_system": ["", "", "", ""],
  "printer_model": "Bambu Lab X1 Carbon", "printer_variant": "0.4", "nozzle_diameter": ["0.4"],
  "filament_colour":       ["#E02020", "#FFFFFF"],
  "filament_multi_colour": ["#E02020", "#FFFFFF"],
  "filament_colour_type":  ["1", "1"],
  "filament_diameter":     ["1.75", "1.75"],
  "filament_ids":          ["GFA00", "GFA00"],
  "filament_extruder_variant": ["Direct Drive Standard", "Direct Drive Standard"],
  "filament_self_index":       ["1", "2"],
  "filament_map": ["1", "1"], "filament_map_mode": "Auto For Flush",
  "filament_volume_map": ["0", "0"], "filament_nozzle_map": ["0", "0"],
  "nozzle_volume_type": ["Standard"],
  "flush_volumes_matrix": ["0", "280", "280", "0"], "flush_volumes_vector": ["140", "140", "140", "140"],
  "flush_multiplier": ["1"],
  "curr_bed_type": "Textured PEI Plate"
}
```

| key | rule |
|---|---|
| `filament_colour` | N `#RRGGBB` strings. **This defines N.** Project-level, never dirties a preset. |
| `filament_multi_colour`, `filament_colour_type` | N each (defaults are 1-element: BS `PrintConfig.cpp:2992-2996`) |
| `filament_settings_id`, `filament_diameter` | N each (gotcha 3) |
| `filament_ids` | N, `filament_id` of each preset (only used for new presets / AMS) |
| `filament_extruder_variant` + `filament_self_index` | equal length ≥ N (gotcha 4); see §4 |
| `filament_map` | N, 1-based logical extruder (single-nozzle printers: all `1`; H2D: 1 = left, 2 = right, verify against a real H2D file) |
| `filament_map_mode` | `Auto For Flush` / `Auto For Match` / `Manual` / `Nozzle Manual` (BS adds `Auto For Quality`) (BS `PrintConfig.cpp:913-919`) |
| `filament_volume_map` / `filament_nozzle_map` | N ints (NozzleVolumeType enum, 0 = Standard) / N |
| `nozzle_volume_type` | one per extruder: `Standard`, `High Flow`, … (BS `PrintConfig.cpp:897-904`) |
| `flush_volumes_matrix` | N×N per extruder (`extruders × N × N`), diagonal 0. `flush_volumes_vector` 2N; `flush_multiplier` one per extruder. Orca's default multiplier is 0.3; Bambu's is 1. |
| `extruder_ams_count` | optional; erased on load (BS `PresetBundle.cpp:5363-5365`) |
| `enable_filament_dynamic_map`, `has_filament_switcher` | omit (Orca erases the first) |

**AppConfig colours are not needed.** Colours on load come only from `filament_colour`. The app-config
`filament_colors` is only the startup state (BS `PresetBundle.cpp:2206-2213`).

---------------------------------------------------------------------------------------------------
## 4. Extruder variants (Bambu 2.x; Orca main has the same machinery)

Keys: printer `printer_extruder_id` / `printer_extruder_variant` / `extruder_variant_list` / `extruder_type`;
process `print_extruder_id` / `print_extruder_variant`; filament `filament_extruder_variant`;
project `filament_self_index`. Variant vectors (`*_options_with_variant`) hold one value per variant entry.

| printer (system profiles, BS resources) | `extruder_variant_list` | `printer_extruder_id` | `printer_extruder_variant` | process | filament (`… @BBL X1C`) |
|---|---|---|---|---|---|
| X1C 0.4 (`BBL/machine/Bambu Lab X1 Carbon 0.4 nozzle.json`) | `["Direct Drive Standard,Direct Drive High Flow"]` | `["1","1"]` | `[Std, HF]` | `print_extruder_id ["1","1"]`, `print_extruder_variant [Std, HF]` | include `fdm_filament_template_direct_dual` → `filament_extruder_variant [Std, HF]`, values ×2 |
| base single (`fdm_bbl_3dp_001_common`, e.g. A1/P1 older variants) | `["Direct Drive Standard"]` | `["1"]` | `[Std]` | ×1 | ×1 |
| H2D 0.4 | `["Std,HF,E3D HF", "Std,HF,TPU HF,E3D HF"]` | `["1","1","1","2","2","2","2"]` | 7 entries | `print_extruder_id` 7 entries | `@BBL H2D`: `[Std, HF, E3D HF]` ×3 |

**How the 3mf stores them.** Saving uses `full_fff_config(apply_extruder=false)` (BS `PresetBundle.cpp:4745-4756`).
It writes printer and process variant vectors unchanged. Filament vectors are concatenated per filament, and
`filament_self_index` repeats the 1-based filament number for each of its variant entries (`:4978-4997`). Example:
2× PLA on X1C → `filament_extruder_variant [Std,HF,Std,HF]`, `filament_self_index [1,1,2,2]`,
`nozzle_temperature [220,220,220,220]`.

On load, when `len(filament_extruder_variant) != N`, each filament's block is sliced via `filament_self_index`
(BS `PresetBundle.cpp:5347-5357`, `5493-5503`). Otherwise element i goes to filament i.

**What a writer emits:**
- **Name-only default mode (all system, or self-anchored):** `filament_extruder_variant` = N × the active variant
  (`"Direct Drive Standard"`, or `"Direct Drive High Flow"` if the user's nozzle is HF), and
  `filament_self_index = [1..N]`. Omit printer/process variant keys (they come from the base).
  `nozzle_diameter` / `extruder_type` sizes = extruder count (H2D: 2).
- **Exact mode (user presets with diff lists, or `--prusa-settings`):** emit the full expanded form, exactly like
  Bambu: each filament's resolved `filament_extruder_variant` block, its value block for every listed key, and the
  matching `filament_self_index`. Printer and process variant keys are emitted with the full lengths of the base
  preset. Compressed form fails for user filament diffs on non-first variants: those are restored from the parent
  (`update_non_diff_values_to_base_config`, `PrintConfig.cpp:10326-10401`) → shown "(modified)".
- **Orca, non-BBL vendors:** profiles have no variant keys (defaults are 1 variant), so emit 1 per filament.
  Orca BBL profiles carry the same variants as Bambu. Orca tolerates a missing `filament_extruder_variant` /
  `filament_self_index`.

---------------------------------------------------------------------------------------------------
## 5. model_settings.config per-plate keys that interact
`<plate>` metadata (BS `bbs_3mf.cpp:344-371`, read `4977-5040`, write `8765-8830`):

| key | effect |
|---|---|
| `bed_type` | string BedType name (`Cool Plate`, `Engineering Plate`, `High Temp Plate`, `Textured PEI Plate`, `Supertack Plate`; Orca adds `Textured Cool Plate`). Overrides the project `curr_bed_type` for that plate; **omit** to follow the global setting. |
| `print_sequence` | `by layer` / `by object` (per-plate override of the process key; omit to keep the process value clean) |
| `first_layer_print_sequence`, `other_layers_print_sequence(_nums)` | per-plate filament order |
| `spiral_mode` | per-plate vase mode |
| `filament_map_mode`, `filament_maps` (space-separated 1-based), `filament_volume_maps` | per-plate grouping; `filament_map_mode="Auto"` is ignored on read |
| `nozzle_volume_type`, `nozzle_diameters` | per-plate nozzle info (BS `:5161-5170`) |

**Project-level `curr_bed_type`:** use the printer's `default_bed_type` (machine_model JSON, e.g. X1C
`Textured PEI Plate`) or the user's `app.curr_bed_type` (Bambu) / `orca_presets[].curr_bed_type` (Orca, int → name
via the enums in gotcha 9). On load Bambu writes it back to `app.curr_bed_type` (BS `Plater.cpp:12164-12177`).

---------------------------------------------------------------------------------------------------
## 6. Recommended algorithms

### Common: `BambuInstall` (mirrors `ref prusa_config.py:304-456`)
```
find(dir?) -> candidates per §1.1 (+ env override), first with <App>.conf
conf = parse leading JSON of <App>.conf
folder = conf.app.preset_folder or "default"
selected():  Bambu: presets.machine / presets.process / presets.filaments[]
             Orca:  m = presets.machine; e = first orca_presets[] with e.machine == m;
                    process = e.process; filaments = [e.filament, e.filament_01, ...];
                    fallback presets.process / presets.filaments
preset(kind, name): user/<folder>/<kind>/<name>.json (+ Orca _local/_subscribed bundles)
                    else system/*/<kind> entry with "name"==name (scan system/<V>.json lists → sub_path)
resolve(): §1.3 (inherits chain + include + nil); cache; .opc-only vendor → None (name-only)
installed_printers(): §1.4
is_installed(kind, name): user file exists, or system entry exists (instantiation true); for filaments any existing one is OK
```
**Bad selected names.** The last project opened may have left a name like `"X(foo.3mf)"` or `"… (modified)"`
selected. If the name is not found:
1. Strip the `(<…>.3mf)` suffix and retry.
2. Fall back to the printer's `default_print_profile` / `default_filament_profile[0]`, like `ref convert.py:211-218`.
3. For a filament whose type differs from the Prusa filament type: `Generic <TYPE> @BBL <suffix>` or an
   `@base`-family preset compatible with the printer (`compatible_printers` contains the printer name), like
   `ref convert.py:114-136`.

### (a) `auto` (default)
1. Read the selection with `selected()`. Pad the filament list to the number of Prusa extruders used by repeating
   the last entry (Bambu does this too: `set_num_filaments` `PresetBundle.cpp:2494-2500`).
2. For each slot, build `inherits_group` / `different_settings_to_system` per §2.3:
   - prefer exact mode when `resolve()` succeeds and the parent is a system preset;
   - otherwise self-anchor (for the print slot always self-anchor if unresolvable, since it has no dialog).
3. Emit §3.1 with `filament_colour` = the Prusa extruder colours (or the painted-colour mapping) and
   `filament_diameter` from the resolved presets (or `1.75`).
4. Copy `printer_model` / `printer_variant` / `nozzle_diameter` / `extruder_type` from the printer if resolvable.
   These are harmless because they are base-replaced.
5. Set the 3mf `Application` metadata = `BambuStudio-<app.version>` (Orca: `OrcaSlicer-<version>`), not newer
   than the installed app.

### (b) a named installed printer
Look up the printer (user, then system). If it exists but is not visible, warn, since it still loads.
- If it is the currently selected printer, use the selected process and filaments.
- Otherwise use the Orca `orca_presets` entry for that machine if one exists. Failing that, use
  `default_print_profile`, and `default_filament_profile` or the model's `default_materials`. Check that each
  candidate's `compatible_printers` contains the printer name (or is empty / the condition passes).

Then continue as in (a).

### (c) an exported file
| input | handling |
|---|---|
| Bambu/Orca `.3mf` or a bare `project_settings.config` | Read `printer_settings_id`, `print_settings_id`, `filament_settings_id`, `inherits_group`, `different_settings_to_system`. If the names are installed → (b). Otherwise copy the file's full config (it is complete) and let it load as project presets (§2.2 last row). |
| `*.json` preset, full form (File > Export > "Export current configs": `PresetBundle::export_current_configs` `:7628-7660`, full resolved config, `from:""`) | type from `printer_settings_id` / `print_settings_id` / `filament_settings_id` keys or `type`. Name installed → (b); otherwise use it as a full printer config (project preset). |
| `*.json` user preset (diff form, has `inherits`) | resolve against the installed system parent; if the parent is missing, report an error. |
| preset bundle `.bbscfg` / `.bbsflmt` / `.zip` (Bambu), `.orca_printer` / `.orca_filament` / `.orca_bundle` / `.zip` (Orca) | zip with `bundle_structure.json` (`printer_config` / `filament_config` / `process_config` lists, `bundle_type`, `printer_preset_name`) + `printer/`, `filament/`, `process/` user JSONs (BS `CreatePresetsDialog.cpp:4068-4160`, import `PresetBundle.cpp:1024-1105`, OS `:2399`). Choose `printer_preset_name`; filaments and process from the bundle; resolve as user presets. |

### (d) keep the Prusa printer (no Bambu printer installed / `--printer prusa`)
1. **Preferred: anchor on a target-app system printer of the same model.** Orca ships Prusa CORE One / MK3.5 / MK3S /
   MK4 / MK4S / MINI / XL (+HF/MMU) (`OS resources/profiles/Prusa.json`). Bambu Studio ships only
   `Prusa MK3S 0.4 nozzle` and `Prusa MINI 0.4 nozzle` (`BS resources/profiles/Prusa.json`).
   - Map the Prusa `printer_model` and nozzle to `"Prusa <Model> <d> nozzle"`.
   - Set `printer_settings_id = "<Prusa preset name>"` (not installed), `inherits_group[last] = <that system printer>`
     and `different_settings_to_system[last]` = the translated keys that differ.
   - Result: a project printer preset; validation passes because the inherit is a system preset.
   - Process and filaments: the vendor's compatible system presets as bases (Orca has Prusa process presets), with
     the translated Prusa values in their diff lists.
2. **Fallback: a standalone external printer.** Set `inherits_group[last] = ""` and use a name that is not
   installed. The result is defaults + the given keys, plus a "Customized Preset" warning. Minimum keys:
   `printer_technology "FFF"`, `printer_model ""` (or a vendor model), `printer_variant`, `nozzle_diameter`,
   `printable_area` (`["0x0","Wx0","WxD","0xD"]`), `printable_height`, `bed_exclude_area []`, `gcode_flavor`,
   `machine_start_gcode` / `machine_end_gcode` / `before_layer_change_gcode` / `layer_change_gcode` /
   `change_filament_gcode` / `machine_pause_gcode` (**translate placeholders** Prusa → Bambu, e.g.
   `[first_layer_temperature]` → `[nozzle_temperature_initial_layer]`, `[first_layer_bed_temperature]` →
   `[bed_temperature_initial_layer_single]`), `retraction_length` / `retraction_speed` / `deretraction_speed` /
   `z_hop` / `retract_lift_above/below`, `extruder_offset`, `extruder_colour`, `single_extruder_multi_material`,
   `machine_max_*`, `max/min_layer_height`, `nozzle_type`, `use_relative_e_distances`, `use_firmware_retraction`,
   `extruder_type ["Direct Drive"]`, `extruder_variant_list ["Direct Drive Standard"]`,
   `printer_extruder_id ["1"]`, `printer_extruder_variant ["Direct Drive Standard"]`,
   `default_nozzle_volume_type ["Standard"]`.
   - Multi-tool (XL): N entries for every extruder vector, and `len(extruder_type) == len(nozzle_diameter)`, or
     Bambu loads geometry only (§2 step 1). Orca derives the filament count from `nozzle_diameter` when
     `single_extruder_multi_material=0` (OS `Preset.cpp:555-572`).
   - Bambu Studio treats it as a third-party printer: no device/AMS features, `curr_bed_type` still drives the bed
     temperatures.
   - Process and filaments in this mode must be project presets too (`--prusa-settings`), because the user's BBL
     process/filament presets are incompatible.

### `--prusa-settings` (project / modified presets)
- **Simplest:** keep the user's preset names in `*_settings_id`, set `inherits_group` = their system base, and list
  the translated keys (+ the user's own diffs) in `different_settings_to_system`, with exact values (§4 exact mode).
  - Print, printer and filament 1 show as `"<preset> (modified)"`.
  - Filaments 2..N become project presets `"<name>(<file>.3mf)"` (§2.2).
- **Uniform alternative:** give every slot a new name (e.g. `"<base> - PrusaSlicer"`) and also embed full configs as
  `Metadata/{process,filament,machine}_settings_<n>.config`. Each is `save_to_json(name, "project", version)` of
  the full preset config including its `*_settings_id` (BS `bbs_3mf.cpp:8500-8535`; reader `:3150-3200` requires
  `*_settings_id`). The ids then match the embedded presets → all slots clean "project presets" (BS
  `Preset.cpp:2423-2429`, `PresetBundle.cpp:703-735`).

### Orca-only alternative: "published" 3MF (OS main 2026-11, not in Bambu)
- **Format:** model metadata `orca_published = "1"`, plus `orca_published_keys` (JSON array),
  `orca_published_config` (INI payload), `orca_published_material_keys` (per-slot JSON with optional
  `color` / `publish_color`) and `orca_published_extruder_layout`. There is no `project_settings.config`.
- **Effect:** Orca keeps the user's **current** presets and overlays only the listed keys (OS
  `bbs_3mf.hpp:159-168`, `Plater.cpp:9343-9460` (flag at 9350), `PresetBundle.cpp:6187-6190`, `6336-6343`).
- **Caveat:** new and unreleased. Older Orca imports it as geometry only. Only an optional target.
