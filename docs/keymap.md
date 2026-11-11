# PrusaSlicer -> Bambu Studio / OrcaSlicer: reverse key mapping

Derived by inverting Panda2Prusa's `keymap.py` (Rule tables) and `translate.py` (special cases),
then re-checking every key, type, default and enum string against the sources:

* Bambu Studio `master` `src/libslic3r/PrintConfig.cpp/.hpp`, `Preset.cpp`, `PresetBundle.cpp`
* OrcaSlicer `main` (same files; this snapshot already has the multi-extruder "variant" machinery, belt printers, IMEX)
* PrusaSlicer `version_2.9.7` (`PrintConfig.cpp/.hpp`, `Preset.cpp`, `PresetBundle.cpp`, plus `Flow.cpp`, `FuzzySkin.cpp` for semantics)

Machine-readable versions: `reverse_keymap.py` (the rules + converters; `python3 reverse_keymap.py` runs
`self_check()`, which verifies every source key against `prusa_schema.OPTIONS`, every target key against
`bambu_schema`/`orca_schema`, every emitted enum string against the target's `ENUM_ACCEPTED`
(the `s_keys_map_*` table), and that every Prusa enum value has a mapping). It currently reports **OK**.

## 0. Schemas (gen_schema.py)

| | Bambu Studio | OrcaSlicer | PrusaSlicer 2.9.7 |
|---|---|---|---|
| OPTIONS (all defs incl. SLA/CLI/placeholders) | 790 | 1004 | 388 (FFF only) |
| DEFAULTS | 787 | 940 (the 64 missing are read-only placeholder vars) | 384 |
| PRINT / FILAMENT / PRINTER keys | 310 / 153 / 143 | 394 / 152 / 208 | 197 / 86 / 91 |
| OBJECT / REGION keys | 93 / 121 | 132 / 208 | 70 / 67 |
| PROJECT keys (`s_project_options`) | 34 | 25 | 3 |
| enum types (`ENUM_MAPS`) | 49 | 65 | – |

Tables in each module: `OPTIONS {key: (type, nullable, enum_values_ui, min, max)}`, `ENUM_TYPE {key: C++ enum}`,
`RATIO_OVER {key: percent base}` (new: from `def->ratio_over`), `ENUM_MAPS {CppEnum: accepted names}`,
`ENUM_ACCEPTED {key: accepted names}`, `DEFAULTS`, `PRINT_KEYS`, `FILAMENT_KEYS`, `PRINTER_KEYS`
(= printer + machine limits + extruder keys, like `Preset::printer_options()`), `MACHINE_LIMITS_KEYS`,
`EXTRUDER_KEYS`, `PROJECT_KEYS`, `OBJECT_KEYS`, `REGION_KEYS`, and for Bambu/Orca `PRINT_VARIANT_KEYS`,
`FILAMENT_VARIANT_KEYS`, `PRINTER_VARIANT_KEYS` (`*_options_with_variant`), `FILAMENT_EXTRUDER_OVERRIDE_KEYS`.

**OPTIONS enum vs ENUM_ACCEPTED.** OPTIONS carries the list the option definition offers in the UI
(`enum_values`); the parser accepts the whole `s_keys_map_<Type>` (legacy aliases included). Example:
Bambu `gcode_flavor` UI = (marlin, klipper) but it parses 13 names; Bambu `top_surface_pattern` UI lists 9
patterns but parses all 26 InfillPattern names. Write values from the UI list when possible.

**DEFAULTS serialization** = `project_settings.config` JSON: scalars as strings (`"0.2"`, `"20%"`, `"1"`/`"0"`
for bools, enum names), vectors as lists of strings (`["60"]`, `["0x0","200x0",...]`), `"nil"` for unset
nullable elements. Floats use C++ ostream default formatting (= Python `%g`). Parsed from `set_default_value(...)`
(C-style casts, enumerators resolved via the enum declarations / keys maps, `#define` constants from
`PrintConfigConstants.hpp`, nested braces, loops: machine limits axes table, `filament_extruder_override_keys`,
`filament_overhang_override_keys`, Orca's `for (key : {...})` and `add_belt_remap` lambdas).

**Validation of the default parser:** run on PrusaSlicer 2.9.7 and compared with the reference
`prusa_schema.DEFAULTS` (which came from `prusa-slicer-console --save`): 343 keys compared, 339 identical;
the 4 differences are only ini quoting (`"...\n"` vs raw text) and `filament_seam_gap_distance`
(ini `15%`, source nil). **Conclusion: the reference DEFAULTS are for 2.9.7 and are reused as-is** (they are
in ini text format: vectors joined with `,` or `;`), source-derived values fill keys they lack.
The reference prusa OPTIONS had 3 keys that are commented out / `#if 0` in 2.9.7
(`infill_only_where_needed`, `seam_preferred_direction`, `seam_preferred_direction_jitter`); the new generator
strips comments and `#if 0` blocks, so they are gone. It also fixes min-bounds the old regex read from commented
lines (e.g. `support_material_contact_distance` has no min).

Sanity checks (Bambu | Orca):
`wall_loops` int 0..1000 def "2" | same. `sparse_infill_pattern` def `cubic` | `crosshatch`; Bambu calls
rectilinear `zig-zag`, Orca `rectilinear` (Orca handle_legacy rewrites `zig-zag`->`rectilinear`).
`filament_colour` strings def `["#00AE42"]` | `["#F2754E"]`. `enable_prime_tower` bool `"0"` | `"0"`.
`wipe_tower_x` floats (one per plate) `["15"]` | `["15"]`, `wipe_tower_y` `["220"]`.
`outer_wall_speed` floats nullable `["60"]` (per extruder variant) in both.

### Structural differences that matter for writing

* **Bambu Studio 2.x and Orca main store most speeds/accelerations/line-width-independent process values as
  vectors** (`PRINT_VARIANT_KEYS`, e.g. `outer_wall_speed`, `default_acceleration`, `travel_speed`,
  `initial_layer_speed`), one element per `print_extruder_variant` (1 for single-nozzle printers, 2 on an H2D).
  Filament keys in `FILAMENT_VARIANT_KEYS` are per filament x variant, printer keys in `PRINTER_VARIANT_KEYS`
  per `printer_extruder_variant`. Write a converted scalar into **every** element of the base preset's vector.
* Many printer keys are **nullable** in Bambu (`nozzle_diameter`, `retraction_length`, machine limits, ...)
  but not in Orca; irrelevant for writing concrete values.
* Line widths: Bambu `float` mm only; Orca `floatorpercent` (% of **nozzle diameter**); Prusa `floatorpercent`
  with % of **layer height** (first layer: of first_layer_height) and 0 = auto. Always resolve to mm.
* Fan speeds: Bambu `ints`, Orca `floats`; layer times likewise. Values copy, but write ints for Bambu.
* `filament_notes`: Bambu scalar string, Orca vector.

## 1. Multi-key encodings (special cases, `special_print` etc. in reverse_keymap.py)

| Prusa | Bambu | Orca | Rule |
|---|---|---|---|
| `support_material` | `enable_support` | = | bool copy |
| `support_material_auto` + `support_material_style` | `support_type` | = | `tree` if style in (organic, tree[legacy]) else `normal`; + `(auto)` if auto else `(manual)`. Values: normal(auto), tree(auto), normal(manual), tree(manual) (both) |
| `support_material_style` | `support_style` | = | grid->`grid`, snug->`snug`, organic->`tree_organic` (Bambu) / `organic` (Orca). Bambu `default` = grid for normal, hybrid for tree |
| (organic tree params) | – | `tree_support_branch_angle_organic`, `tree_support_branch_diameter_organic`, `tree_support_branch_distance_organic` | Orca's organic trees use the *_organic keys; also write them |
| `ironing` + `ironing_type` | `ironing_type` | = | ironing=0 -> `no ironing`; else top/topmost/solid (same names) |
| `external_perimeters_first` | `wall_sequence` | = | 1 -> `outer wall/inner wall`, 0 -> `inner wall/outer wall` (`inner-outer-inner wall` has no Prusa source) |
| `complete_objects` | `print_sequence` | = | 1 -> `by object`, 0 -> `by layer` |
| `top_one_perimeter_type` | `top_one_wall_type` | `only_one_wall_top` (bool) | none->`not apply`, top->`all top`, topmost->`topmost`; Orca: none->0, top/topmost->1 (lossy; Orca's legacy handler does the same for Bambu files). Orca also has `min_width_top_surface` |
| `gap_fill_enabled` | – | `gap_fill_target` | 1->`everywhere`, 0->`nowhere` (Orca default is `nowhere`!). Bambu cannot disable gap fill (reference derived gap_fill_enabled from gap_infill_speed>0; Bambu min is 0, behaviour of 0 undocumented) |
| `bed_temperature`, `first_layer_bed_temperature` (per filament) | `cool_plate_temp`, `eng_plate_temp`, `hot_plate_temp`, `textured_plate_temp`, `supertack_plate_temp` + `*_initial_layer` | + `textured_cool_plate_temp(_initial_layer)` | **write all plate types** (see 1.1) |
| (none) | `curr_bed_type` (project) | = | set explicitly; default `Textured PEI Plate` (see 1.1) |
| `seam_gap_distance` (mm or % of nozzle) | `seam_gap` (**percent only**, of nozzle) | `seam_gap` (mm or %) | Bambu: mm -> 100*mm/nozzle %; negative (overlap) values dropped (min 0) |
| `xy_size_compensation` | `xy_contour_compensation` + `xy_hole_compensation` | = | contour = v, hole = **-v** (Prusa grows/shrinks the whole slice, so holes move the opposite way; Bambu: positive hole value = bigger holes) |
| `support_material_bottom_contact_distance` | `support_bottom_z_distance` | = | Prusa 0 = "same as top" -> copy `support_material_contact_distance` |
| `overhang_speed_0..3` | `overhang_totally_speed`, `overhang_4_4_speed`, `overhang_3_4_speed`, `overhang_2_4_speed` | `overhang_4_4_speed`=min(slot0,slot1), `overhang_3_4_speed`, `overhang_2_4_speed` | order flips (Prusa names slots by overlap, Bambu by overhang). Prusa % is of external_perimeter_speed -> Bambu mm/s (resolved); Orca % base is outer_wall_speed (same) so % is kept. `overhang_1_4_speed` (10-25 %) has no Prusa source. Orca dropped overhang_totally_speed (legacy-ignored) |
| `first_layer_speed` as % | `initial_layer_speed` (mm/s) | = | Prusa % scales every first-layer speed; Bambu/Orca need one value -> % of perimeter_speed, warned |
| `first_layer_infill_speed` = 0 | `initial_layer_infill_speed` | = | Prusa 0 = use first_layer_speed -> copy initial_layer_speed (Bambu min 1) |
| `*_extrusion_width` (0, %, mm) | `*_line_width` | = | 0 -> Prusa auto width (`Flow::auto_extrusion_width`: 1.125 x nozzle for perimeters/infill/solid, 1.0 x nozzle for top/support; 0 per-feature falls back to `extrusion_width` first); % -> of layer_height (first layer: of first_layer_height); write mm |
| `perimeter_extruder` etc. | `wall_filament`, `sparse_infill_filament`, `solid_infill_filament`, `support_filament`, `support_interface_filament` (1-based, 0 = object's filament) | `outer_wall_filament_id`+`inner_wall_filament_id`, `sparse_infill_filament_id`, `internal_solid_filament_id`+`top_surface_filament_id`+`bottom_surface_filament_id`, `support_filament`, `support_interface_filament`, `wipe_tower_filament` (0 = default) | remap extruder numbers -> filament slots. At print level Prusa's "1" usually means "default": write 0 unless the user really pins a feature (Orca's own legacy handler rewrites perimeter_extruder=1 -> 0) |
| `infill_every_layers` > 1 | `infill_combination`=1 | + `infill_combination_max_layer_height` = n x layer_height | lossy (count not preserved) |
| `travel_ramping_lift` (per extruder) | `z_hop_types` | = | 1 -> `Slope Lift`, 0 -> `Normal Lift` (Bambu default is `Spiral Lift`, Orca `Slope Lift`: always write). Orca also `travel_slope` (min 1 deg) |
| `filament_travel_ramping_lift` | `filament_z_hop_types` | = | nil stays nil |
| `wiping_volumes_matrix` / `multimaterial_purging` x `filament_purge_multiplier` | `flush_volumes_matrix` (project, row-major n x n per nozzle) | = | custom matrix if `wiping_volumes_use_custom_matrix`, else purge x multiplier[j]; set `flush_multiplier` = ["1"]. Bambu purges into the chute, so its volumes are normally larger than Prusa tower purges |
| `multimaterial_purging` | `filament_prime_volume` (per filament) | `prime_volume` | prime-tower volume per change |
| `wipe_into_infill`, `wipe_into_objects` (per object) | `flush_into_infill`, `flush_into_objects` | = | bool; Bambu also `flush_into_support` (default 1) |
| `wipe_tower_x/y/rotation_angle` (2.9: per bed in `Metadata/Prusa_Slicer_wipe_tower_information.xml`; legacy scalar keys) | `wipe_tower_x`, `wipe_tower_y` = one entry **per plate**; `wipe_tower_rotation_angle` scalar | = | copy per bed->plate; both use the tower's front-left corner; Bambu's tower footprint (prime_tower_width, brim, depth from prime volume) differs, re-clamp to bed |
| `filament_shrinkage_compensation_xy` (2 % = part shrinks 2 %) | `filament_shrink` | = (+ `filament_shrinkage_compensation_z` from Prusa's z key) | shrink = 100 % - comp (e.g. 2 % -> 98 %); Orca min 50 % max 150 %, Bambu min 10 % |
| `filament_abrasive` | `required_nozzle_HRC` | = | 1 -> 40 (Bambu's CF/GF profiles); 0 -> keep base |
| `max_volumetric_speed` (print) + `filament_max_volumetric_speed` | `filament_max_volumetric_speed` | = | Prusa 0 = unlimited / off: result = min(non-zero of both); both 0 -> keep base (Bambu/Orca require > 0) |
| `temperature`/`first_layer_temperature` | `nozzle_temperature(_initial_layer)` + widen `nozzle_temperature_range_low/high` | = | Bambu warns when the temperature leaves the filament's recommended range |
| `nozzle_high_flow` (per extruder) | `nozzle_volume_type` (project) `High Flow`/`Standard`, plus printer_extruder_variant | = | lossy: variants select different Bambu presets |
| `remaining_times` | – (Bambu always writes M73) | `disable_m73` = !remaining_times | |
| `filament_load_time`/`filament_unload_time` (per filament) | `machine_load_filament_time`/`machine_unload_filament_time` (printer scalar) | = | max over used filaments |
| `extruder_clearance_height` | `extruder_clearance_height_to_rod` + `extruder_clearance_height_to_lid` | = | same value in both |
| `max_layer_height` = 0 | `max_layer_height` | = | Prusa 0 = 75 % of nozzle |

### 1.1 Bed temperature: which keys?

Write the Prusa value into **all** plate-type keys of each filament (Bambu: 5 pairs, Orca: 6 pairs) and set
`curr_bed_type`:

* Bambu reads only the pair of the plate in use (`get_bed_temp_key(curr_bed_type)`, PrintConfig.hpp), but
  `Print::validate()` (Print.cpp:1811) **refuses to slice** when the used filament's first-layer value for the
  current plate is 0 ("Plate %d: %s does not support filament %s"). Writing only `curr_bed_type`'s pair leaves
  the others at the base preset's values (often 0 for e.g. Supertack/Cool Plate on PETG), so switching the plate
  in the GUI silently changes temperatures or blocks slicing.
* PrusaSlicer has no bed-type concept. Default `curr_bed_type` = `Textured PEI Plate` (Prusa satin/textured
  sheets are PEI); let the user override. Orca also has a printer `default_bed_type`. Valid values (UI):
  Bambu `Cool Plate`, `Engineering Plate`, `High Temp Plate`, `Textured PEI Plate`, `Supertack Plate`;
  Orca adds `Textured Cool Plate`. (`Default Plate` is parser-only; Orca legacy maps `SuperTack Plate`.)
* Bambu also stores a per-plate override in `Metadata/model_settings.config`: `<plate>` metadata
  `key="bed_type"` (read into the plate's `curr_bed_type`, bbs_3mf.cpp); likewise per-plate `print_sequence`.
  Omit it (plate uses the project value) or keep it consistent.

## 2. Enum name maps (verified against `s_keys_map_*`)

| Prusa option | Prusa value | Bambu value | Orca value | Bambu/Orca -> Prusa (reference) |
|---|---|---|---|---|
| fill_pattern / sparse_infill_pattern | rectilinear | `zig-zag` | `rectilinear` | zig-zag/rectilinear->rectilinear |
| | stars | `tri-hexagon` | `tri-hexagon` | tri-hexagon->stars |
| | alignedrectilinear, grid, triangles, cubic, line, concentric, honeycomb, 3dhoneycomb, gyroid, hilbertcurve, archimedeanchords, octagramspiral, adaptivecubic, supportcubic, lightning, zigzag | same | same | crosshatch->grid, crosszag/lockedzag->zigzag, 2dlattice->grid; Orca-only quartercubic, lateral-honeycomb, lateral-lattice, tpmsd, tpmsfk have no Prusa value |
| | monotonic, monotoniclines (parser-only for sparse) | `zig-zag` | `rectilinear` | |
| top_fill_pattern / top_surface_pattern, bottom_fill_pattern / bottom_surface_pattern | rectilinear | `zig-zag` | `rectilinear` | |
| | monotonic | `monotonic` | `monotonic` | |
| | monotoniclines | `monotonicline` | `monotonicline` | monotonicline, globalmonotonicline(Bambu)->monotoniclines |
| | alignedrectilinear, concentric, hilbertcurve, archimedeanchords, octagramspiral | same | same | Orca `spiralinset` has no Prusa value |
| | (bottom) grid/stars/cubic/gyroid/... | `monotonic` (lossy; parser would accept, UI does not offer) | `monotonic` | |
| support_material_pattern / support_base_pattern | rectilinear, rectilinear-grid, honeycomb | same | same | default/lightning/hollow -> rectilinear |
| support_material_interface_pattern / support_interface_pattern | auto, rectilinear, concentric | same | same | rectilinear_interlaced/grid -> rectilinear |
| seam_position | random / nearest / aligned / rear | random / nearest / aligned / `back` | same (+ `aligned_back`) | back->rear |
| scarf_seam_placement / seam_slope_type | nowhere / contours / everywhere | none / external / all | same | |
| top_one_perimeter_type | none / top / topmost | top_one_wall_type: `not apply` / `all top` / `topmost` | only_one_wall_top: 0/1/1 | |
| fuzzy_skin | none / external / all | none (=None, allow paint) / external / all | none ("Painted only") / external / all | Prusa `all` = outer perimeter of contours **and holes** (FuzzySkin.cpp should_fuzzify) = Bambu/Orca `all` ("Contour and hole"); `allwalls` (all perimeters) and Orca `hole` have no Prusa value; reference mapped allwalls->all, disabled_fuzzy->none. Orca default is `disabled_fuzzy`, so always write it |
| brim_type | no_brim / outer_only / inner_only / outer_and_inner | same | same | auto_brim, brim_ears (+Orca painted, leading_edge_only) -> outer_only. Bambu/Orca default auto_brim: always write |
| draft_shield | disabled / limited / enabled | same | disabled / enabled (limited -> **enabled**; Orca's own legacy handler maps limited->disabled) | |
| ensure_vertical_shell_thickness | disabled / partial / enabled | same | none / ensure_moderate / ensure_all (`ensure_critical_only` unused) | Orca legacy: 1->ensure_all, 0->ensure_moderate |
| arc_fitting / enable_arc_fitting | disabled / emit_center | bool 0 / 1 | enum disabled / enabled (+ sparse_infill) | |
| perimeter_generator / wall_generator | classic / arachne | same | same | |
| slicing_mode | regular / even_odd / close_holes | same | same | |
| gcode_flavor | reprap, reprapfirmware, repetier, teacup, makerware, marlin, marlin2, klipper, sailfish, mach3, machinekit, smoothie, no-extrusion | all parsed; UI only marlin, klipper -> marlin2->`marlin` | all parsed; UI marlin, klipper, reprapfirmware, repetier, marlin2 | |
| support_material_style + auto | grid/snug/organic | support_type normal/tree (auto/manual), support_style grid/snug/tree_organic | ... / organic | tree_* -> organic, snug->snug, else grid |
| ironing + ironing_type | top/topmost/solid | ironing_type `no ironing`/top/topmost/solid | same | |
| only_retract_when_crossing_perimeters | 0/1 | reduce_infill_retraction_mode `Disabled`/`Enabled` (also `Auto`) | reduce_infill_retraction bool | Auto -> keep base |
| cooling_slowdown_logic | uniform_cooling / consistent_surface (/ proportional legacy) | same (proportional->uniform_cooling) | – | |
| gcode_label_objects | disabled / octoprint / firmware | – (always labels) | bool 0/1/1 | |
| toolchange_ordering | optimized / cyclic | – | default / cyclic | |
| thumbnails_format | PNG / JPG / QOI | – (`thumbnail_size` points) | same (+BTT_TFT, COLPIC) | |
| filament_type | PLA, PET, ABS, ASA, FLEX, HIPS, EDGE, NGEN, PA, NYLON, PVA, PC, PP, PEI, PEEK, PEKK, POM, PSU, PVDF, SCAFF | PET->PETG, FLEX->TPU, NYLON->PA, EDGE/NGEN->PETG, rest same text (Bambu list has no PEI/PEEK/PEKK/POM/PSU/PVDF/SCAFF: free text kept) | NYLON->PA, EDGE/NGEN->PETG; PET, FLEX exist | TPU*->FLEX |
| curr_bed_type | – | Cool Plate, Engineering Plate, High Temp Plate, Textured PEI Plate, Supertack Plate | + Textured Cool Plate | |
| z_hop_types | (travel_ramping_lift) | Auto Lift, Normal Lift, Slope Lift, Spiral Lift | same | Slope Lift -> travel_ramping_lift=1 |

## 3. Percent bases and units

| Prusa key | Prusa % base | Bambu | Orca |
|---|---|---|---|
| first_layer_height | layer_height | float mm -> resolve | float mm -> resolve |
| external_perimeter_speed, small_perimeter_speed | perimeter_speed | outer_wall_speed mm/s; small_perimeter_speed % of **outer**_wall_speed -> resolve to mm/s | same as Bambu |
| solid_infill_speed | infill_speed | mm/s -> resolve | mm/s -> resolve |
| top_solid_infill_speed | solid_infill_speed (chain) | resolve | resolve |
| support_material_interface_speed | support_material_speed | resolve | resolve |
| first_layer_speed | scales all first-layer speeds | single mm/s | single mm/s |
| first_layer_infill_speed | solid_infill_speed (0 = first_layer_speed) | mm/s (min 1) | mm/s |
| overhang_speed_N | external_perimeter_speed | mm/s -> resolve | % of outer_wall_speed -> keep |
| *_extrusion_width | layer_height (first layer: first_layer_height); 0 = auto | mm | mm or % of **nozzle** -> write mm |
| infill_overlap | perimeter_extrusion_width | infill_wall_overlap % of inner_wall_line_width -> keep | same |
| infill_anchor(_max) | infill_extrusion_width | sparse_infill_anchor(_max) % of sparse_infill_line_width -> keep | infill_anchor(_max) -> keep |
| support_material_xy_spacing | external_perimeter_extrusion_width | support_object_xy_distance mm -> resolve | same |
| seam_gap_distance | nozzle_diameter | seam_gap % only -> mm converted to % | mm or % -> copy |
| scarf_seam_start_height | layer_height | seam_slope_start_height percent -> keep (mm is invalid for Bambu) | floatorpercent -> keep |
| ironing_flowrate | (percent of flow) | ironing_flow percent -> keep | keep |
| automatic_infill_combination_max_layer_height | nozzle_diameter | – | infill_combination_max_layer_height (same base) -> keep |
| accelerations | plain mm/s², 0 = default | mm/s², 0 = default (sparse_infill_acceleration may be % of default) | same; bridge_acceleration % of outer_wall_acceleration |
| speeds = 0 | "auto" (volumetric-limited) | not allowed / meaningless -> keep base preset value, warn | same |
| travel_speed_z = 0 | use travel speed | same meaning | same |
| deretract_speed = 0 | same as retract speed | same | same |
| support_material_threshold = 0 | auto | Bambu min 1 -> 30 (its default) | Orca 0 = use `support_threshold_overlap` (closest to auto) |
| support_material_bottom_interface_layers = -1 | same as top | -1 = same as top | same |
| filament_max_volumetric_speed = 0 | unlimited | must be > 0 | must be > 0 |
| retract_before_wipe | percent | percent | percent |
| temperatures, fan % | °C, % | ints | fan: floats |

## 4. Rule tables (generated from reverse_keymap.py)

### PRINT_RULES (process)

| Prusa | Bambu | Orca | Conversion | Note |
|---|---|---|---|---|
| `layer_height` | `layer_height` | = |  |  |
| `first_layer_height` | `initial_layer_print_height` | = | % of `layer_height` → absolute |  |
| `perimeters` | `wall_loops` | = |  |  |
| `spiral_vase` | `spiral_mode` | = | to_bool |  |
| `slice_closing_radius` | `slice_closing_radius` | = |  |  |
| `slicing_mode` | `slicing_mode` | = | enum: same: regular, even_odd, close_holes |  |
| `top_solid_layers` | `top_shell_layers` | = |  |  |
| `top_solid_min_thickness` | `top_shell_thickness` | = |  |  |
| `bottom_solid_layers` | `bottom_shell_layers` | = |  |  |
| `bottom_solid_min_thickness` | `bottom_shell_thickness` | = |  |  |
| `ensure_vertical_shell_thickness` | `ensure_vertical_shell_thickness` | = | enum: same: disabled, partial, enabled; Orca: enum: disabled→none, partial→ensure_moderate, enabled→ensure_all | Orca uses a 4-level enum; partial->ensure_moderate is approximate |
| `extra_perimeters_on_overhangs` | — | `extra_perimeters_on_overhangs` | to_bool |  |
| `avoid_crossing_perimeters` | `reduce_crossing_wall` | = | to_bool |  |
| `avoid_crossing_perimeters_max_detour` | `max_travel_detour_distance` | = | % copied verbatim | both: mm or % of the direct travel |
| `thin_walls` | `detect_thin_wall` | = | to_bool |  |
| `overhangs` | `detect_overhang_wall` | = | to_bool |  |
| `seam_position` | `seam_position` | = | enum: rear→back; same: random, nearest, aligned |  |
| `staggered_inner_seams` | — | `staggered_inner_seams` | to_bool |  |
| `seam_gap_distance` | `seam_gap` | = | non_negative; % of `nozzle_diameter` kept as % for bambu+orca | Bambu: percent of nozzle only (mm resolved to %, see special case); negative Prusa values dropped |
| `only_one_perimeter_first_layer` | `only_one_wall_first_layer` | = | to_bool |  |
| `top_one_perimeter_type` | `top_one_wall_type` | `only_one_wall_top` | enum: none→not apply, top→all top; same: topmost; Orca: enum: none→0, top→1, topmost→1 | Orca only has a bool; 'topmost' becomes 'all top surfaces' |
| `scarf_seam_placement` | `seam_slope_type` | = | enum: nowhere→none, contours→external, everywhere→all |  |
| `scarf_seam_only_on_smooth` | `seam_slope_conditional` | = | to_bool |  |
| `scarf_seam_start_height` | `seam_slope_start_height` | = | % of `layer_height` kept as % for bambu+orca |  |
| `scarf_seam_entire_loop` | `seam_slope_entire_loop` | = | to_bool |  |
| `scarf_seam_length` | `seam_slope_min_length` | = |  |  |
| `scarf_seam_on_inner_perimeters` | `seam_slope_inner_walls` | = | to_bool |  |
| `fill_density` | `sparse_infill_density` | = |  |  |
| `fill_pattern` | `sparse_infill_pattern` | = | enum: stars→tri-hexagon, rectilinear→zig-zag, monotonic→zig-zag, monotoniclines→zig-zag; same: alignedrectilinear, grid, triangles, cubic, line, concentric, honeycomb, 3dhoneycomb, gyroid, hilbertcurve, archimedeanchords, octagramspiral, adaptivecubic, supportcubic, lightning, zigzag; Orca: enum: stars→tri-hexagon, monotonic→rectilinear, monotoniclines→rectilinear; same: alignedrectilinear, grid, triangles, cubic, line, concentric, honeycomb, 3dhoneycomb, gyroid, hilbertcurve, archimedeanchords, octagramspiral, adaptivecubic, supportcubic, lightning, zigzag, rectilinear |  |
| `top_fill_pattern` | `top_surface_pattern` | = | enum: monotoniclines→monotonicline, grid→monotonic, triangles→monotonic, stars→monotonic, cubic→monotonic, line→monotonic, honeycomb→monotonic, 3dhoneycomb→monotonic, gyroid→monotonic, adaptivecubic→monotonic, supportcubic→monotonic, lightning→monotonic, zigzag→monotonic, rectilinear→zig-zag; same: monotonic, alignedrectilinear, concentric, hilbertcurve, archimedeanchords, octagramspiral; Orca: enum: monotoniclines→monotonicline, grid→monotonic, triangles→monotonic, stars→monotonic, cubic→monotonic, line→monotonic, honeycomb→monotonic, 3dhoneycomb→monotonic, gyroid→monotonic, adaptivecubic→monotonic, supportcubic→monotonic, lightning→monotonic, zigzag→monotonic; same: monotonic, alignedrectilinear, concentric, hilbertcurve, archimedeanchords, octagramspiral, rectilinear |  |
| `bottom_fill_pattern` | `bottom_surface_pattern` | = | enum: monotoniclines→monotonicline, grid→monotonic, triangles→monotonic, stars→monotonic, cubic→monotonic, line→monotonic, honeycomb→monotonic, 3dhoneycomb→monotonic, gyroid→monotonic, adaptivecubic→monotonic, supportcubic→monotonic, lightning→monotonic, zigzag→monotonic, rectilinear→zig-zag; same: monotonic, alignedrectilinear, concentric, hilbertcurve, archimedeanchords, octagramspiral; Orca: enum: monotoniclines→monotonicline, grid→monotonic, triangles→monotonic, stars→monotonic, cubic→monotonic, line→monotonic, honeycomb→monotonic, 3dhoneycomb→monotonic, gyroid→monotonic, adaptivecubic→monotonic, supportcubic→monotonic, lightning→monotonic, zigzag→monotonic; same: monotonic, alignedrectilinear, concentric, hilbertcurve, archimedeanchords, octagramspiral, rectilinear |  |
| `automatic_infill_combination` | `infill_combination` | = | to_bool |  |
| `automatic_infill_combination_max_layer_height` | — | `infill_combination_max_layer_height` | % of `nozzle_diameter` kept as % for orca |  |
| `fill_angle` | `infill_direction` | = |  |  |
| `bridge_angle` | `bridge_angle` | = |  |  |
| `solid_infill_below_area` | `minimum_sparse_infill_area` | = |  |  |
| `only_retract_when_crossing_perimeters` | `reduce_infill_retraction_mode` | `reduce_infill_retraction` | enum: 1→Enabled, 0→Disabled; Orca: to_bool |  |
| `infill_first` | `is_infill_first` | = | to_bool |  |
| `infill_overlap` | `infill_wall_overlap` | = | % of `perimeter_extrusion_width` kept as % for bambu+orca |  |
| `infill_anchor` | `sparse_infill_anchor` | `infill_anchor` | % of `infill_extrusion_width` kept as % for bambu+orca |  |
| `infill_anchor_max` | `sparse_infill_anchor_max` | `infill_anchor_max` | % of `infill_extrusion_width` kept as % for bambu+orca |  |
| `ironing_flowrate` | `ironing_flow` | = | % copied verbatim |  |
| `ironing_speed` | `ironing_speed` | = |  |  |
| `ironing_spacing` | `ironing_spacing` | = |  |  |
| `fuzzy_skin` | `fuzzy_skin` | = | enum: same: none, external, all |  |
| `fuzzy_skin_thickness` | `fuzzy_skin_thickness` | = |  |  |
| `fuzzy_skin_point_dist` | `fuzzy_skin_point_distance` | = |  |  |
| `perimeter_speed` | `inner_wall_speed` | = | positive_or_skip |  |
| `small_perimeter_speed` | `small_perimeter_speed` | = | positive_or_skip; % of `perimeter_speed` → absolute | Prusa % is of perimeter_speed, Bambu/Orca % of outer_wall_speed: resolved to mm/s |
| `external_perimeter_speed` | `outer_wall_speed` | = | positive_or_skip; % of `perimeter_speed` → absolute |  |
| `infill_speed` | `sparse_infill_speed` | = | positive_or_skip |  |
| `solid_infill_speed` | `internal_solid_infill_speed` | = | positive_or_skip; % of `infill_speed` → absolute |  |
| `top_solid_infill_speed` | `top_surface_speed` | = | positive_or_skip; % of `solid_infill_speed` → absolute |  |
| `support_material_speed` | `support_speed` | = | positive_or_skip |  |
| `support_material_interface_speed` | `support_interface_speed` | = | positive_or_skip; % of `support_material_speed` → absolute |  |
| `bridge_speed` | `bridge_speed` | = | positive_or_skip |  |
| `gap_fill_speed` | `gap_infill_speed` | = | positive_or_skip |  |
| `travel_speed` | `travel_speed` | = | positive_or_skip |  |
| `travel_speed_z` | `travel_speed_z` | = |  | 0 = use travel speed in all three |
| `first_layer_speed` | `initial_layer_speed` | = | positive_or_skip | Prusa '%' scales every first-layer speed: special case |
| `first_layer_infill_speed` | `initial_layer_infill_speed` | = | positive_or_skip | Prusa 0 = use first_layer_speed; % of solid infill speed: special case |
| `enable_dynamic_overhang_speeds` | `enable_overhang_speed` | = | to_bool |  |
| `max_volumetric_extrusion_rate_slope_positive` | — | `max_volumetric_extrusion_rate_slope` |  | Orca has one slope for both directions; Bambu dropped the feature |
| `default_acceleration` | `default_acceleration` | = |  |  |
| `perimeter_acceleration` | `inner_wall_acceleration` | = |  |  |
| `external_perimeter_acceleration` | `outer_wall_acceleration` | = |  |  |
| `infill_acceleration` | `sparse_infill_acceleration` | = |  |  |
| `solid_infill_acceleration` | — | `internal_solid_infill_acceleration` |  |  |
| `top_solid_infill_acceleration` | `top_surface_acceleration` | = |  |  |
| `bridge_acceleration` | — | `bridge_acceleration` |  |  |
| `first_layer_acceleration` | `initial_layer_acceleration` | = |  |  |
| `travel_acceleration` | `travel_acceleration` | = |  |  |
| `travel_short_distance_acceleration` | `travel_short_distance_acceleration` | — |  |  |
| `wipe_tower_acceleration` | `prime_tower_acceleration` | — |  |  |
| `skirts` | `skirt_loops` | = |  |  |
| `skirt_distance` | `skirt_distance` | = |  |  |
| `skirt_height` | `skirt_height` | = |  |  |
| `min_skirt_length` | — | `min_skirt_length` |  |  |
| `draft_shield` | `draft_shield` | = | enum: same: disabled, limited, enabled; Orca: enum: limited→enabled; same: disabled, enabled |  |
| `brim_width` | `brim_width` | = |  |  |
| `brim_separation` | `brim_object_gap` | = |  |  |
| `brim_type` | `brim_type` | = | enum: same: no_brim, outer_only, inner_only, outer_and_inner | Bambu default is auto_brim: always write it |
| `support_material_threshold` | `support_threshold_angle` | = | zero_to(30); Orca: copy | Prusa 0 = automatic (Bambu min 1 -> 30, its default); Orca 0 = use support_threshold_overlap |
| `support_material_enforce_layers` | `enforce_support_layers` | = |  |  |
| `raft_layers` | `raft_layers` | = |  |  |
| `raft_first_layer_density` | `raft_first_layer_density` | = |  |  |
| `raft_first_layer_expansion` | `raft_first_layer_expansion` | = |  |  |
| `raft_contact_distance` | `raft_contact_distance` | = |  |  |
| `raft_expansion` | `raft_expansion` | = |  |  |
| `support_material_pattern` | `support_base_pattern` | = | enum: same: rectilinear, rectilinear-grid, honeycomb |  |
| `support_material_spacing` | `support_base_pattern_spacing` | = |  |  |
| `support_material_synchronize_layers` | `independent_support_layer_height` | = | invert_bool |  |
| `support_material_angle` | `support_angle` | = |  |  |
| `support_material_interface_layers` | `support_interface_top_layers` | = |  |  |
| `support_material_bottom_interface_layers` | `support_interface_bottom_layers` | = |  | -1 = same as top in all three |
| `support_material_interface_pattern` | `support_interface_pattern` | = | enum: same: auto, rectilinear, concentric |  |
| `support_material_interface_spacing` | `support_interface_spacing` | = |  |  |
| `support_material_interface_contact_loops` | `support_interface_loop_pattern` | = | to_bool |  |
| `support_material_contact_distance` | `support_top_z_distance` | = |  |  |
| `support_material_bottom_contact_distance` | `support_bottom_z_distance` | = |  | Prusa 0 = same as top: special case |
| `support_material_buildplate_only` | `support_on_build_plate_only` | = | to_bool |  |
| `support_material_xy_spacing` | `support_object_xy_distance` | = | % of `external_perimeter_extrusion_width` → absolute |  |
| `support_tree_angle` | `tree_support_branch_angle` | = |  | Orca organic trees read tree_support_branch_angle_organic too: special case |
| `support_tree_angle_slow` | — | `tree_support_angle_slow` |  |  |
| `support_tree_branch_diameter` | `tree_support_branch_diameter` | = |  |  |
| `support_tree_branch_diameter_angle` | `tree_support_branch_diameter_angle` | = |  |  |
| `support_tree_branch_distance` | `tree_support_branch_distance` | = |  |  |
| `support_tree_tip_diameter` | `tree_support_tip_diameter` | = |  |  |
| `support_tree_top_rate` | — | `tree_support_top_rate` | % copied verbatim |  |
| `dont_support_bridges` | `bridge_no_support` | = | to_bool |  |
| `thick_bridges` | `thick_bridges` | = | to_bool |  |
| `perimeter_extruder` | `wall_filament` | `outer_wall_filament_id` |  | Orca also has inner_wall_filament_id: write both |
| `infill_extruder` | `sparse_infill_filament` | `sparse_infill_filament_id` |  |  |
| `solid_infill_extruder` | `solid_infill_filament` | `internal_solid_filament_id` |  | Orca also top_surface_filament_id / bottom_surface_filament_id |
| `support_material_extruder` | `support_filament` | = |  |  |
| `support_material_interface_extruder` | `support_interface_filament` | = |  |  |
| `wipe_tower_extruder` | — | `wipe_tower_filament` |  |  |
| `ooze_prevention` | `ooze_prevention` | = | to_bool |  |
| `standby_temperature_delta` | `standby_temperature_delta` | = |  |  |
| `interface_shells` | `interface_shells` | = | to_bool |  |
| `wipe_tower` | `enable_prime_tower` | = | to_bool |  |
| `wipe_tower_width` | `prime_tower_width` | = |  |  |
| `wipe_tower_brim_width` | `prime_tower_brim_width` | = |  |  |
| `wipe_tower_no_sparse_layers` | `wipe_tower_no_sparse_layers` | = | to_bool |  |
| `wipe_tower_extra_spacing` | `prime_tower_infill_gap` | `wipe_tower_extra_spacing` |  | Orca keeps Prusa's key (type2 tower); Bambu's infill gap is the closest (both percent, min 100%) |
| `wipe_tower_bridging` | — | `wipe_tower_bridging` |  |  |
| `wipe_tower_cone_angle` | — | `wipe_tower_cone_angle` |  |  |
| `wipe_tower_extra_flow` | — | `wipe_tower_extra_flow` |  |  |
| `mmu_segmented_region_max_width` | `mmu_segmented_region_max_width` | = |  |  |
| `mmu_segmented_region_interlocking_depth` | `mmu_segmented_region_interlocking_depth` | = |  |  |
| `interlocking_beam` | `interlocking_beam` | = | to_bool |  |
| `interlocking_beam_width` | `interlocking_beam_width` | = |  |  |
| `interlocking_orientation` | `interlocking_orientation` | = |  |  |
| `interlocking_beam_layer_count` | `interlocking_beam_layer_count` | = |  |  |
| `interlocking_depth` | `interlocking_depth` | = |  |  |
| `interlocking_boundary_avoidance` | `interlocking_boundary_avoidance` | = |  |  |
| `extrusion_width` | `line_width` | = | % of `layer_height` → absolute |  |
| `first_layer_extrusion_width` | `initial_layer_line_width` | = | % of `first_layer_height` → absolute |  |
| `perimeter_extrusion_width` | `inner_wall_line_width` | = | % of `layer_height` → absolute |  |
| `external_perimeter_extrusion_width` | `outer_wall_line_width` | = | % of `layer_height` → absolute |  |
| `infill_extrusion_width` | `sparse_infill_line_width` | = | % of `layer_height` → absolute |  |
| `solid_infill_extrusion_width` | `internal_solid_infill_line_width` | = | % of `layer_height` → absolute |  |
| `top_infill_extrusion_width` | `top_surface_line_width` | = | % of `layer_height` → absolute |  |
| `support_material_extrusion_width` | `support_line_width` | = | % of `layer_height` → absolute |  |
| `bridge_flow_ratio` | `bridge_flow` | = |  |  |
| `elefant_foot_compensation` | `elefant_foot_compensation` | = |  |  |
| `xy_size_compensation` | `xy_contour_compensation` | = |  | Prusa offsets the whole slice; also set xy_hole_compensation = -value: special case |
| `gcode_resolution` | `resolution` | = |  | Bambu/Orca 'resolution' is the G-code path simplification (= Prusa gcode_resolution); Prusa 'resolution' (input mesh simplification) has no equivalent |
| `arc_fitting` | `enable_arc_fitting` | = | arc_fitting_bambu; Orca: enum: emit_center→enabled; same: disabled |  |
| `perimeter_generator` | `wall_generator` | = | enum: same: classic, arachne |  |
| `wall_transition_length` | `wall_transition_length` | = | % copied verbatim |  |
| `wall_transition_filter_deviation` | `wall_transition_filter_deviation` | = | % copied verbatim |  |
| `wall_transition_angle` | `wall_transition_angle` | = |  |  |
| `wall_distribution_count` | `wall_distribution_count` | = |  |  |
| `min_feature_size` | `min_feature_size` | = | % copied verbatim |  |
| `min_bead_width` | `min_bead_width` | = | % copied verbatim |  |
| `output_filename_format` | `filename_format` | = |  | placeholder names differ ([input_filename_base] works in all three) |
| `post_process` | `post_process` | = |  |  |
| `gcode_comments` | — | `gcode_comments` | to_bool |  |
| `gcode_label_objects` | — | `gcode_label_objects` | Orca: enum: disabled→0, octoprint→1, firmware→1 |  |
| `notes` | `process_notes` | `notes` |  |  |
| `toolchange_ordering` | — | `toolchange_ordering` | Orca: enum: optimized→default; same: cyclic |  |
| `prefer_clockwise_movements` | `print_in_clockwise` | `wall_direction` | to_bool; Orca: enum: 1→cw, 0→ccw | Prusa: printer key; Bambu (hidden, comDevelop) / Orca: process keys |
| `complete_objects` | `print_sequence` | = | enum: 1→by object, 0→by layer |  |

### FILAMENT_RULES (per filament)

| Prusa | Bambu | Orca | Conversion | Note |
|---|---|---|---|---|
| `filament_colour` | `filament_colour` | = |  |  |
| `filament_diameter` | `filament_diameter` | = |  |  |
| `filament_type` | `filament_type` | = | filament_type_bambu; Orca: filament_type_orca |  |
| `filament_soluble` | `filament_soluble` | = | to_bool |  |
| `filament_abrasive` | `required_nozzle_HRC` | = | abrasive_to_hrc |  |
| `filament_notes` | `filament_notes` | = |  | Bambu: one scalar string for the preset; Orca: vector |
| `filament_max_volumetric_speed` | `filament_max_volumetric_speed` | = | positive_or_skip | Prusa 0 = unlimited; Bambu/Orca need > 0. Also min() with print max_volumetric_speed |
| `extrusion_multiplier` | `filament_flow_ratio` | = |  |  |
| `filament_density` | `filament_density` | = |  |  |
| `filament_cost` | `filament_cost` | = |  |  |
| `filament_minimal_purge_on_wipe_tower` | `filament_minimal_purge_on_wipe_tower` | = |  |  |
| `temperature` | `nozzle_temperature` | = |  |  |
| `first_layer_temperature` | `nozzle_temperature_initial_layer` | = |  |  |
| `chamber_temperature` | `chamber_temperatures` | `chamber_temperature` |  |  |
| `chamber_minimal_temperature` | — | `chamber_minimal_temperature` |  |  |
| `idle_temperature` | — | `idle_temperature` |  | Prusa nil = off; Orca 0 = off |
| `fan_always_on` | `reduce_fan_stop_start_freq` | = | to_bool |  |
| `cooling` | `slow_down_for_layer_cooling` | = | to_bool | Prusa 'cooling' also gates the fan logic; Bambu/Orca only the slowdown |
| `cooling_slowdown_logic` | `cooling_slowdown_logic` | — | enum: proportional→uniform_cooling; same: uniform_cooling, consistent_surface |  |
| `cooling_perimeter_transition_distance` | `cooling_perimeter_transition_distance` | — |  |  |
| `min_fan_speed` | `fan_min_speed` | = |  |  |
| `max_fan_speed` | `fan_max_speed` | = |  |  |
| `bridge_fan_speed` | `overhang_fan_speed` | = |  |  |
| `disable_fan_first_layers` | `close_fan_the_first_x_layers` | = |  |  |
| `full_fan_speed_layer` | `full_fan_speed_layer` | = |  |  |
| `fan_below_layer_time` | `fan_cooling_layer_time` | = |  |  |
| `slowdown_below_layer_time` | `slow_down_layer_time` | = |  |  |
| `min_print_speed` | `slow_down_min_speed` | = |  |  |
| `start_filament_gcode` | `filament_start_gcode` | = |  | macro placeholders differ |
| `end_filament_gcode` | `filament_end_gcode` | = |  | macro placeholders differ |
| `filament_shrinkage_compensation_xy` | `filament_shrink` | = | compensation_to_shrink |  |
| `filament_shrinkage_compensation_z` | — | `filament_shrinkage_compensation_z` | compensation_to_shrink |  |
| `filament_retract_length` | `filament_retraction_length` | = |  |  |
| `filament_retract_lift` | `filament_z_hop` | = |  |  |
| `filament_retract_lift_above` | `filament_retract_lift_above` | = |  |  |
| `filament_retract_lift_below` | `filament_retract_lift_below` | = |  |  |
| `filament_retract_speed` | `filament_retraction_speed` | = |  |  |
| `filament_deretract_speed` | `filament_deretraction_speed` | = |  |  |
| `filament_retract_restart_extra` | `filament_retract_restart_extra` | = |  |  |
| `filament_retract_before_travel` | `filament_retraction_minimum_travel` | = |  |  |
| `filament_retract_layer_change` | `filament_retract_when_changing_layer` | = |  |  |
| `filament_wipe` | `filament_wipe` | = |  |  |
| `filament_retract_before_wipe` | `filament_retract_before_wipe` | = |  |  |
| `filament_retract_length_toolchange` | `filament_retract_length_toolchange` | = |  |  |
| `filament_retract_restart_extra_toolchange` | `filament_retract_restart_extra_toolchange` | = |  |  |
| `filament_cooling_final_speed` | — | `filament_cooling_final_speed` |  |  |
| `filament_cooling_initial_speed` | — | `filament_cooling_initial_speed` |  |  |
| `filament_cooling_moves` | — | `filament_cooling_moves` |  |  |
| `filament_loading_speed` | — | `filament_loading_speed` |  |  |
| `filament_loading_speed_start` | — | `filament_loading_speed_start` |  |  |
| `filament_unloading_speed` | — | `filament_unloading_speed` |  |  |
| `filament_unloading_speed_start` | — | `filament_unloading_speed_start` |  |  |
| `filament_toolchange_delay` | — | `filament_toolchange_delay` |  |  |
| `filament_ramming_parameters` | — | `filament_ramming_parameters` |  |  |
| `filament_multitool_ramming` | — | `filament_multitool_ramming` |  |  |
| `filament_multitool_ramming_flow` | — | `filament_multitool_ramming_flow` |  |  |
| `filament_multitool_ramming_volume` | — | `filament_multitool_ramming_volume` |  |  |
| `filament_stamping_distance` | — | `filament_stamping_distance` |  |  |
| `filament_stamping_loading_speed` | — | `filament_stamping_loading_speed` |  |  |

### FILAMENT_TO_PRINTER_RULES

| Prusa | Bambu | Orca | Conversion | Note |
|---|---|---|---|---|
| `filament_load_time` | `machine_load_filament_time` | = |  | take the max over used filaments |
| `filament_unload_time` | `machine_unload_filament_time` | = |  | take the max over used filaments |

### PRINTER_RULES (machine scalars)

| Prusa | Bambu | Orca | Conversion | Note |
|---|---|---|---|---|
| `bed_shape` | `printable_area` | = |  |  |
| `max_print_height` | `printable_height` | = |  |  |
| `bed_custom_texture` | `bed_custom_texture` | = |  |  |
| `bed_custom_model` | `bed_custom_model` | = |  |  |
| `gcode_flavor` | `gcode_flavor` | = | enum: marlin2→marlin; same: marlin, klipper, reprap, reprapfirmware, repetier, teacup, makerware, sailfish, smoothie, mach3, machinekit, no-extrusion; Orca: enum: same: marlin, marlin2, klipper, reprap, reprapfirmware, repetier, teacup, makerware, sailfish, smoothie, mach3, machinekit, no-extrusion | Bambu UI offers marlin/klipper only but parses all; marlin2 -> marlin for Bambu |
| `use_relative_e_distances` | `use_relative_e_distances` | = | to_bool |  |
| `use_firmware_retraction` | `use_firmware_retraction` | = | to_bool |  |
| `single_extruder_multi_material` | `single_extruder_multi_material` | = | to_bool |  |
| `printer_model` | `printer_model` | = |  | Bambu keys system behaviour on its own model names: usually leave unset |
| `printer_variant` | `printer_variant` | = |  |  |
| `printer_notes` | `printer_notes` | = |  |  |
| `silent_mode` | `silent_mode` | — | to_bool |  |
| `extruder_clearance_radius` | `extruder_clearance_max_radius` | `extruder_clearance_radius` |  |  |
| `extruder_clearance_height` | `extruder_clearance_height_to_rod` | = |  | also extruder_clearance_height_to_lid = same value |
| `template_custom_gcode` | `template_custom_gcode` | = |  |  |
| `pause_print_gcode` | `machine_pause_gcode` | = |  |  |
| `start_gcode` | `machine_start_gcode` | = |  | placeholders differ: see GCODE_PLACEHOLDERS |
| `end_gcode` | `machine_end_gcode` | = |  | placeholders differ |
| `before_layer_gcode` | `before_layer_change_gcode` | = |  |  |
| `layer_gcode` | `layer_change_gcode` | = |  |  |
| `toolchange_gcode` | `change_filament_gcode` | = |  |  |
| `between_objects_gcode` | `printing_by_object_gcode` | = |  |  |
| `z_offset` | — | `z_offset` |  |  |
| `thumbnails` | — | `thumbnails` |  | same 'WxH/FMT,...' syntax |
| `high_current_on_filament_swap` | — | `high_current_on_filament_swap` | to_bool |  |
| `parking_pos_retraction` | — | `parking_pos_retraction` |  |  |
| `cooling_tube_length` | — | `cooling_tube_length` |  |  |
| `cooling_tube_retraction` | — | `cooling_tube_retraction` |  |  |
| `extra_loading_move` | — | `extra_loading_move` |  |  |
| `thumbnails_format` | — | `thumbnails_format` | Orca: enum: same: PNG, JPG, QOI |  |
| `single_extruder_multi_material_priming` | — | `single_extruder_multi_material_priming` | to_bool |  |
| `remaining_times` | — | `disable_m73` | invert_bool | Bambu always emits M73 |
| `machine_limits_usage` | — | `emit_machine_limits_to_gcode` | Orca: enum: emit_to_gcode→1, time_estimate_only→0, ignore→0 |  |
| `multimaterial_purging` | — | `prime_volume` |  | Bambu: filament_prime_volume per filament |
| `default_print_profile` | `default_print_profile` | = |  |  |

### EXTRUDER_RULES (machine, per extruder)

| Prusa | Bambu | Orca | Conversion | Note |
|---|---|---|---|---|
| `nozzle_diameter` | `nozzle_diameter` | = |  |  |
| `min_layer_height` | `min_layer_height` | = |  |  |
| `max_layer_height` | `max_layer_height` | = |  | Prusa 0 = 75% of nozzle: special case |
| `extruder_offset` | `extruder_offset` | = |  |  |
| `extruder_colour` | `extruder_colour` | = |  |  |
| `retract_length` | `retraction_length` | = |  |  |
| `retract_lift` | `z_hop` | = |  |  |
| `retract_lift_above` | `retract_lift_above` | = |  |  |
| `retract_lift_below` | `retract_lift_below` | = |  |  |
| `retract_speed` | `retraction_speed` | = |  |  |
| `deretract_speed` | `deretraction_speed` | = |  | 0 = same as retraction speed in all three |
| `retract_before_wipe` | `retract_before_wipe` | = |  |  |
| `retract_restart_extra` | `retract_restart_extra` | = |  |  |
| `retract_before_travel` | `retraction_minimum_travel` | = |  |  |
| `wipe` | `wipe` | = | to_bool |  |
| `retract_layer_change` | `retract_when_changing_layer` | = | to_bool |  |
| `retract_length_toolchange` | `retract_length_toolchange` | = |  |  |
| `retract_restart_extra_toolchange` | `retract_restart_extra_toolchange` | = |  |  |
| `travel_slope` | — | `travel_slope` |  | Orca min 1 deg; only meaningful with Slope Lift |

### Machine limits (`MACHINE_LIMIT_RULES`)

Prusa `machine_max_feedrate_{x,y,z,e}` -> `machine_max_speed_{x,y,z,e}`; `machine_max_acceleration_{x,y,z,e,extruding,retracting,travel}`,
`machine_max_jerk_{x,y,z,e}`, `machine_min_extruding_rate`, `machine_min_travel_rate` -> same names. All are
`[normal, silent]` pairs in all three. Orca additionally `machine_max_junction_deviation` (same name as Prusa).
`machine_limits_usage` (emit_to_gcode / time_estimate_only / ignore): Bambu has no switch (Bambu firmware ignores
M201/M203; limits are used for time estimates), Orca `emit_machine_limits_to_gcode` = (usage == emit_to_gcode).

### Per object / per part

Prusa per-object and per-volume settings (`Metadata/Slic3r_PE_model.config`) go through `PRINT_RULES`
restricted to targets in the target's `OBJECT_KEYS | REGION_KEYS` (`object_rules(flavor)`), plus the special
cases above (support, ironing, wall_sequence, overhang speeds, extrusion widths) and `OBJECT_SPECIAL`:
`extruder` -> `extruder` (1-based, 0 = inherit), `wipe_into_infill` -> `flush_into_infill`,
`wipe_into_objects` -> `flush_into_objects`. Bambu's GUI offers only a subset of PrintObjectConfig/PrintRegionConfig
per object, but libslic3r applies all of them. Prusa layer-range settings map to Bambu `<range>` entries the same way.

## 5. Lossy / ambiguous cases (most important first)

1. **G-code macros** (`start_gcode`, `end_gcode`, `before_layer_gcode`, `layer_gcode`, `toolchange_gcode`,
   `start/end_filament_gcode`, `between_objects_gcode`): placeholder names differ
   (`[first_layer_temperature]` -> `[nozzle_temperature_initial_layer]`, `[first_layer_bed_temperature]` ->
   `[bed_temperature_initial_layer_single]`, ...; see `GCODE_PLACEHOLDERS`, not exhaustive) and Prusa macros use
   Prusa-only commands/conditionals. For a Bambu printer use the Bambu preset's macros; for a generic Orca printer a
   placeholder rename usually suffices (Orca keeps several Prusa aliases). `color_change_gcode` has no slot
   (Bambu/Orca use `machine_pause_gcode`/M600 in `change_filament_gcode`). `gcode_substitutions`, `post_process`
   semantics: post_process maps 1:1, substitutions are lost.
2. **Bed temperature / plate type**: Prusa has none; all plate keys are written and `curr_bed_type` is a guess.
3. **Speeds set to 0 (auto)** and **first_layer_speed as %**: no Bambu/Orca equivalent; base preset values are
   kept or a scalar is computed. Prusa `max_print_speed` (auto-speed cap) and print-level `max_volumetric_speed`
   only survive as a cap on `filament_max_volumetric_speed`.
4. **Extrusion widths**: Prusa % (of layer height) and 0 (auto) are resolved to mm using the first extruder's
   nozzle; multi-nozzle-diameter setups lose per-extruder auto widths. `automatic_extrusion_widths` (2.9) is a
   GUI-side recalculation (libslic3r only invalidates on it), so the stored widths are what slicing uses.
5. **Support style**: Prusa `organic` -> Bambu `tree_organic`/Orca `organic`; Prusa grid/snug normal supports map
   1:1, but Bambu tree_slim/strong/hybrid have no source. Prusa "support on enforcers only" = support_material=1,
   auto=0 -> `enable_support`=1 + `*(manual)`. Prusa `support_material_threshold=0` (auto) has no Bambu value
   (written as 30). `support_material_closing_radius`, `support_material_with_sheath`,
   `support_tree_branch_diameter_double_wall`, `support_tree_angle_slow` (Orca has it) are lost on Bambu.
6. **Overhang speeds**: 4 Prusa slots vs Bambu 5 bands (1/4 has no source) and Orca 4 bands (slot 0 and 1 merged).
   Prusa dynamic fan speeds (`enable_dynamic_fan_speeds`, `overhang_fan_speed_0..3`) have no equivalent beyond
   Bambu/Orca's single `overhang_fan_speed` + `overhang_fan_threshold` (keep `bridge_fan_speed` -> `overhang_fan_speed`).
7. **Wall order** `inner-outer-inner` and Bambu `is_infill_first` interplay: Prusa only has external_perimeters_first
   + infill_first (both carried). Orca `wall_infill_order` is legacy only.
8. **top_one_perimeter_type=topmost** -> Orca bool only (applies to all top surfaces).
9. **draft_shield=limited** -> Orca has no limited mode (mapped to enabled; Orca's own legacy maps to disabled).
10. **ensure_vertical_shell_thickness=partial** -> Orca `ensure_moderate` is an approximation.
11. **Gap fill off** is impossible in Bambu; Orca `gap_fill_target=nowhere`.
12. **Seam gap**: Bambu takes only a percent >= 0; negative (overlapping) Prusa values are dropped.
    `filament_seam_gap_distance` (per-filament override) has no equivalent.
13. **xy_size_compensation** splits into contour + hole compensation with opposite signs; check holes.
14. **Filament type names**: PET->PETG, FLEX->TPU, NYLON->PA, EDGE/NGEN->PETG are judgment calls; Bambu keys
    behaviour (AMS, temps, cooling) on these names. `filament_abrasive` -> `required_nozzle_HRC=40` is a convention.
15. **Purging / wipe tower**: Prusa's tower purges everything on the tower; Bambu purges into the chute
    (`flush_volumes_matrix`) and only primes on the tower (`filament_prime_volume`). Orca can emulate Prusa
    (`wipe_tower_type=type2`, `purge_in_prime_tower`, MMU ramming keys carried 1:1). Prusa `wipe_tower_extra_spacing`
    -> Bambu `prime_tower_infill_gap` is only roughly equivalent.
16. **Multi-bed (2.9) -> plates**: wipe tower position and bed type are per plate in Bambu; Prusa 2.9 has per-bed
    tower positions in its own XML.
17. **Per-extruder vs per-variant vectors**: Prusa per-extruder printer vectors (retraction, nozzle) map to Bambu
    per-extruder vectors, but Bambu 2.x process vectors are per *extruder variant*, not per tool; a Prusa XL
    (5 tools) maps to a 1-variant Bambu/Orca process.
18. **Fuzzy skin**: Prusa `all` = contours + holes (= Bambu/Orca `all`), not Bambu `allwalls`. Prusa fuzzy
    painting works with `none` (same as Bambu/Orca `none`).
19. **resolution**: Bambu/Orca `resolution` is Prusa's `gcode_resolution`; Prusa's `resolution` (mesh
    simplification) is lost.
20. **fan_always_on -> reduce_fan_stop_start_freq**, **cooling -> slow_down_for_layer_cooling** are close but not
    identical (Prusa `cooling` also gates fan control).

## 6. Prusa keys with no Bambu / Orca equivalent

Print: `avoid_crossing_curled_overhangs`, `extra_perimeters`, `first_layer_acceleration_over_raft`,
`first_layer_speed_over_raft`, `over_bridge_speed` (solid infill above bridges), `solid_infill_every_layers`,
`scarf_seam_max_segment_length` (Bambu/Orca use `seam_slope_steps`), `support_material_closing_radius`,
`support_material_with_sheath`, `support_tree_branch_diameter_double_wall`, `gcode_substitutions`,
`bed_temperature_extruder`, `max_print_speed`, `resolution` (mesh), `automatic_extrusion_widths` (flag only),
`custom_parameters_print`.
Filament: `enable_dynamic_fan_speeds`, `overhang_fan_speed_0..3` (only partially), `filament_infill_max_speed`,
`filament_infill_max_crossing_speed`, `filament_seam_gap_distance`, `filament_spool_weight`,
`filament_travel_lift_before_obstacle`, `filament_travel_max_lift`, `filament_travel_slope`,
`filament_flush_volume`/`filament_flush_speed` (Prusa XL nozzle flush; Bambu `filament_flush_volumetric_speed`/
`filament_flush_temp` are for a different mechanism), `custom_parameters_filament`.
Printer: `autoemit_temperature_commands`, `binary_gcode`, `color_change_gcode`, `travel_lift_before_obstacle`,
`travel_max_lift`, `use_volumetric_e`, `used_filament_compensation`, `variable_layer_height`, `nozzle_high_flow`
(only via variant selection), `host_type`/`print_host`/`printhost_*` (physical printer, not project data),
`printer_technology`, `custom_parameters_printer`. Bambu-only lost: `silent_mode` exists in Bambu, not Orca;
`z_offset`, `thumbnails`, MMU keys exist in Orca, not Bambu.
Preset bookkeeping (`inherits`, `compatible_*`, `*_settings_id`, `default_*_profile`, `*_vendor`): rewrite, don't map.

## 7. Prusa keys NOT in the reference mapping that have a clear equivalent (added here)

| Prusa | Bambu | Orca |
|---|---|---|
| `gcode_resolution` | `resolution` | `resolution` (reference mapped resolution<-resolution, which is a different setting) |
| `output_filename_format` | `filename_format` | = |
| `post_process` | `post_process` | = |
| `between_objects_gcode` | `printing_by_object_gcode` | = |
| `bed_custom_texture`, `bed_custom_model` | same | same |
| `extruder_offset`, `extruder_colour` | same | same |
| `prefer_clockwise_movements` | `print_in_clockwise` (hidden) | `wall_direction` cw/ccw |
| `infill_every_layers` | `infill_combination` (lossy) | + `infill_combination_max_layer_height` |
| `automatic_infill_combination_max_layer_height` | – | `infill_combination_max_layer_height` |
| `max_volumetric_speed` (print) | cap on `filament_max_volumetric_speed` | = |
| `max_volumetric_extrusion_rate_slope_positive/negative` | – (Bambu commented the defs out) | `max_volumetric_extrusion_rate_slope` (min of both) |
| `solid_infill_acceleration`, `bridge_acceleration` | – | `internal_solid_infill_acceleration`, `bridge_acceleration` |
| `extra_perimeters_on_overhangs`, `staggered_inner_seams`, `support_tree_top_rate`, `support_tree_angle_slow` | – | same names (`tree_support_top_rate`, `tree_support_angle_slow`) |
| `min_skirt_length`, `gcode_comments`, `gcode_label_objects`, `toolchange_ordering` | – | same (label: bool; ordering optimized->default) |
| `wipe_tower_extruder`, `wipe_tower_bridging`, `wipe_tower_cone_angle`, `wipe_tower_extra_flow`, `wipe_tower_extra_spacing` | (`prime_tower_infill_gap` ~ extra_spacing) | `wipe_tower_filament`, same names |
| `chamber_temperature`, `chamber_minimal_temperature`, `idle_temperature` | `chamber_temperatures` | `chamber_temperature`, `chamber_minimal_temperature`, `idle_temperature` |
| `filament_shrinkage_compensation_z` | – | `filament_shrinkage_compensation_z` (as 100 % - x) |
| MMU: `filament_cooling_*`, `filament_loading_speed*`, `filament_unloading_speed*`, `filament_toolchange_delay`, `filament_ramming_parameters`, `filament_multitool_ramming*`, `filament_stamping_*`, `cooling_tube_*`, `parking_pos_retraction`, `extra_loading_move`, `high_current_on_filament_swap`, `single_extruder_multi_material_priming` | – | same names |
| `z_offset`, `thumbnails`, `thumbnails_format`, `machine_max_junction_deviation`, `travel_slope` | – | same names |
| `remaining_times` | – | `disable_m73` (inverted) |
| `machine_limits_usage` | – | `emit_machine_limits_to_gcode` |
| `multimaterial_purging` | `filament_prime_volume` | `prime_volume` |
| `travel_ramping_lift`, `filament_travel_ramping_lift` | `z_hop_types`, `filament_z_hop_types` | = |
| `nozzle_high_flow` | `nozzle_volume_type` (lossy) | = |
| `filament_vendor` (scalar) | `filament_vendor` (per filament) | = |

## 8. Bambu vs Orca key-name divergences hit by the mapping

Bambu `sparse_infill_anchor(_max)` = Orca `infill_anchor(_max)`; Bambu `top_one_wall_type` (enum) = Orca
`only_one_wall_top` (bool); Bambu `reduce_infill_retraction_mode` (Disabled/Auto/Enabled) = Orca
`reduce_infill_retraction` (bool); Bambu `wall_filament`/`sparse_infill_filament`/`solid_infill_filament` = Orca
`outer_wall_filament_id`+`inner_wall_filament_id`/`sparse_infill_filament_id`/`internal_solid_filament_id`
(+top/bottom); Bambu `process_notes` = Orca `notes`; Bambu `extruder_clearance_max_radius` = Orca
`extruder_clearance_radius`; Bambu `chamber_temperatures` = Orca `chamber_temperature`; Bambu
`machine_switch_extruder_time` = Orca `machine_tool_change_time`; Bambu `thumbnail_size` = Orca `thumbnails`;
Bambu `enable_arc_fitting` bool = Orca enum; Bambu `ensure_vertical_shell_thickness` 3 levels = Orca 4 levels;
Bambu `seam_gap` percent = Orca floatorpercent; Bambu line widths float = Orca floatorpercent; Orca has no
`overhang_totally_speed`, `travel_short_distance_acceleration`, `prime_tower_acceleration`, `silent_mode`,
`cooling_slowdown_logic`, `cooling_perimeter_transition_distance`; Bambu has no `internal_solid_infill_acceleration`,
`bridge_acceleration`, `extra_perimeters_on_overhangs`, `staggered_inner_seams`, `tree_support_top_rate`,
`gap_fill_target`, `wipe_tower_filament`. Default values also differ (e.g. `fuzzy_skin` none vs disabled_fuzzy,
`z_hop_types` Spiral vs Slope Lift, `sparse_infill_pattern` cubic vs crosshatch, `prime_tower_width` 35 vs 60,
`flush_multiplier` 1 vs 0.3, `filament_colour`): never rely on target defaults for keys Prusa sets.
The full name diff (keys only in one of them) is reproducible from the two schemas (`set(B.OPTIONS) ^ set(O.OPTIONS)`).
