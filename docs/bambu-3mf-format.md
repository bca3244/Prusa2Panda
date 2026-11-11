# Bambu Studio / OrcaSlicer .3mf project format — converter spec (from PrusaSlicer 2.9)

Abbreviations for citations (all paths under `scratchpad/`):
- **B** = `src/BambuStudio/src/libslic3r/Format/bbs_3mf.cpp`
- **O** = `src/OrcaSlicer/src/libslic3r/Format/bbs_3mf.cpp`
- **P** = `src/prusa297/3mf.cpp`; `extra/p3.cpp` = PrusaSlicer 2.9.2 3mf.cpp
- **MB** = `src/prusa297/MultipleBeds.cpp`
- **REF** = `ref/Panda2Prusa/panda2prusa/*.py` (reverse-direction reference converter)
- Bambu GUI/other files are cited by name; they were fetched into `extra/` (GLGizmoText_bbs.*, bbs_3DBed.*, orca_GLGizmoEmboss.cpp, orca_GLGizmoSVG.cpp, prusa297_Plater.cpp). PartPlate.cpp, Plater.cpp, Model.cpp, PrintConfig.cpp, TriangleSelector.cpp, PresetBundle.cpp, Config.cpp line refs are in the Bambu tree unless prefixed "Orca".

Line numbers are approximate to within a few lines (master branches at fetch time).

---

## 1. Zip entries

### 1.1 Entry table

| Entry | Req? | Notes | Cite |
|---|---|---|---|
| `[Content_Types].xml` | required (OPC) | Defaults: rels, model, png, gcode | B:7338-7380 |
| `_rels/.rels` | **required** | First rel with 3dmodel type = start part; written LAST by Bambu | B:5574-5590, 7380-7440 |
| `3D/3dmodel.model` | **required** | Root model: metadata, component-only objects, build | B:7553-8247 |
| `3D/_rels/3dmodel.model.rels` | required if sub-models used (always, for Bambu style) | Lists `3D/Objects/*.model`; its PRESENCE is also what stops Bambu treating the file as a Prusa file | B:7440-7552; Bambu 3mf.cpp:286-345 |
| `3D/Objects/object_<N>.model` | required (one per ModelObject) | Meshes, one `<object>` per volume | B:7700-7900 |
| `Metadata/model_settings.config` | strongly recommended | Object/part names, types, per-object/part config, plates, assemble | B:8612-8980 |
| `Metadata/project_settings.config` | recommended | JSON full print/filament/printer config. Loaded only if Application is BambuStudio-* (Bambu) | B:2259-2271 |
| `Metadata/slice_info.config` | optional (Bambu writes it) | Header only when unsliced | B:9046-9120 |
| `Metadata/plate_<n>.png`, `plate_<n>_small.png`, `plate_no_light_<n>.png`, `top_<n>.png`, `pick_<n>.png` | optional | Thumbnails; stored uncompressed (STORE) | bbs_3mf.hpp:25-34; B:6826-6900 |
| `Metadata/plate_<n>.json` | optional | bbox / pattern info for plate (sliced data) | bbs_3mf.hpp:25-34 |
| `Metadata/layer_heights_profile.txt` | optional | Variable layer height | B:8249-8282 |
| `Metadata/layer_config_ranges.xml` | optional | Height-range modifiers | B:~8285-8340 |
| `Metadata/custom_gcode_per_layer.xml` | optional | Color changes / pauses, per plate | B:9381-9436 |
| `Metadata/cut_information.xml` | optional | Cut ids/connectors | B:8983-9045 |
| `Metadata/brim_ear_points.txt` | optional | Brim ears | B:~9300 |
| `Metadata/filament_sequence.json` | optional; safe to omit | B:3718-3767, 9499-9537 |
| `Metadata/process_settings_N.config`, `filament_settings_N.config`, `machine_settings_N.config` | optional | Embedded user presets; skip | B:~7100-7200 |
| `Metadata/plate_<n>.gcode` (+ .md5) | optional | Only for sliced projects; don't emit | B:~6950 |
| `Auxiliaries/...` | optional | Aux files | B:~7290 |

Bambu write order: content types, thumbnails, model + model rels + object files, layer heights, layer ranges, brim, custom gcode, project config, presets, model_settings, cut info, slice_info, filament_sequence, aux, `_rels/.rels` (B:6826-7315). The reader iterates entries **in zip order** (B:2290-2440). Order matters in one place: slice_info is parsed after model_settings. Keep Bambu's order.

### 1.2 `[Content_Types].xml`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Default Extension="gcode" ContentType="text/x.gcode"/>
</Types>
```
(B:7338-7380)

### 1.3 `_rels/.rels`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
 <Relationship Target="/Metadata/plate_1.png" Id="rel-2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>
 <Relationship Target="/Metadata/plate_1.png" Id="rel-4" Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-middle"/>
 <Relationship Target="/Metadata/plate_1_small.png" Id="rel-5" Type="http://schemas.bambulab.com/package/2021/cover-thumbnail-small"/>
</Relationships>
```
Thumbnail rels are optional. Omit them when no PNG is emitted (B:7380-7440).

### 1.4 `3D/_rels/3dmodel.model.rels`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/Objects/object_1.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
 <Relationship Target="/3D/Objects/object_2.model" Id="rel-2" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
```
How the path is derived: take the start part path, insert `_rels/`, and append `.rels`. Each Target is loaded by an ObjectImporter, in parallel, before the root model is read (B:5574-5590, 2200-2250).

### 1.5 Sub-model `3D/Objects/object_<backup_id>.model`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">
 <metadata name="BambuStudio:3mfVersion">1</metadata>
 <resources>
  <object id="1" p:UUID="00010000-81cb-4c03-9d28-80fed5dfa1dc" type="model">
   <mesh>
    <vertices><vertex x="..." y="..." z="..."/>...</vertices>
    <triangles><triangle v1="0" v2="1" v3="2" paint_color="4" paint_supports="..." paint_seam="..."/>...</triangles>
   </mesh>
  </object>
  <object id="2" ...>   <!-- next volume of same ModelObject -->
 </resources>
 <build/>
</model>
```
- One `<object>` per ModelVolume. Vertices are in **volume-local** coordinates (not baked). Formatted with `%.9g` (B:7700-7900).
- Sub-object UUID = `%08x` of `(volume_index + (backup_id << 16))` followed by `-81cb-4c03-9d28-80fed5dfa1dc` (B:~7760). Shared-mesh objects use the suffix `-71cb-...`. The UUIDs are not validated on read, so any unique value works.
- `type` must be `model` or `other` (B:451-456).
- Triangle paint attributes: `paint_supports`, `paint_seam`, `paint_color`, `paint_fuzzy_skin` (Bambu), and `face_property` (B:109-388). Orca adds `paint_texture_0..7`.

### 1.6 Root `3D/3dmodel.model`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">
 <metadata name="Application">BambuStudio-02.01.00.00</metadata>
 <metadata name="BambuStudio:3mfVersion">1</metadata>
 <metadata name="CopyRight"></metadata>
 <metadata name="CreationDate">2026-11-11</metadata>
 <metadata name="Description"></metadata>
 <metadata name="Designer"></metadata>
 <metadata name="DesignerCover"></metadata>
 <metadata name="DesignerUserId"></metadata>
 <metadata name="License"></metadata>
 <metadata name="ModificationDate">2026-11-11</metadata>
 <metadata name="Origin"></metadata>
 <metadata name="Title"></metadata>
 <resources>
  <object id="3" p:UUID="00000001-61cb-4c03-9d28-80fed5dfa1dc" type="model">
   <components>
    <component p:path="/3D/Objects/object_1.model" objectid="1" p:UUID="00010000-b206-40ff-9872-83e8017abed1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>
    <component p:path="/3D/Objects/object_1.model" objectid="2" p:UUID="00010001-b206-40ff-9872-83e8017abed1" transform="..."/>
   </components>
  </object>
 </resources>
 <build p:UUID="2c7c17d8-22b5-4d84-8835-1976022ea369">
  <item objectid="3" p:UUID="00000003-b1ec-4553-aec9-835e5b724bb4" transform="1 0 0 0 1 0 0 0 1 128 128 10" printable="1"/>
 </build>
</model>
```
- Metadata comes from a sorted `std::map`, so it is alphabetical (B:7553-7650). Only Application and BambuStudio:3mfVersion matter.
- **Id allocation** (B:7660-7700): each volume gets a sequential id from a running counter. The object id is the next number after its last volume, then the counter increments. Example: object A has 2 volumes → ids 1, 2, object 3; object B starts at 4. Ids only need to be unique **per file**, so the scheme is optional.
- Component `p:path` must exactly equal the sub-model path **with a leading "/"**. Lookup is `(path string, objectid)` with exact string match (B:4481-4503).
- Component `transform` = volume matrix (volume → object).
- One `<build><item>` per **ModelInstance**. Bambu writes the item `p:path` as empty or omitted (B:8220-8247). `transform` = instance matrix.
- **Matrix string**: 12 numbers ordered for c in 0..3, for r in 0..2: `m(r,c)`. That is column-major 3x3 followed by the translation, i.e. standard 3MF (B:~8100). The reference uses the same convention (REF transform.py).

---

## 2. Project detection, version checks, Application string

### 2.1 Bambu loader branch (Bambu Model.cpp:490-529; Bambu 3mf.cpp:286-345)
- `PrusaFileParser` check: if **`3D/_rels/3dmodel.model.rels` is absent** AND the Application metadata contains `PrusaSlicer`, the file is `From_Prusa`. It goes through the Prusa loader with `load_config=false` and the GUI warns "not from Bambu Lab". **So always emit the model rels.**
- Otherwise the bbs_3mf loader is used.

### 2.2 Inside bbs_3mf
| Condition | Effect | Cite |
|---|---|---|
| Application starts with `BambuStudio-` | `m_is_bbl_3mf = true`; version parsed from suffix | B:4576-4581 |
| no `BambuStudio-` Application | Bambu: `dont_load_config = true`. project_settings, custom gcode, slice_info, presets, thumbnails, filament_sequence are **not loaded** | B:2259-2271 |
| Orca | also accepts `OrcaSlicer-` and the `<metadata name="OrcaSlicer">` tag; its dont_load_config check is commented out | O:4176-4194, 1983-1992 |
| model_settings parse failure | fatal only if bbl | B:~2450 |
| non-bbl | objects with multiple instances are split | B:~2700 |
| `BambuStudio:3mfVersion` > VERSION_BBS_3MF (1) | error / reject | B:~4590 |

### 2.3 Bambu Plater (Plater.cpp:11796-11960)
- Semver: Bambu's 4-part `AA.BB.CC.DD` becomes major AA, minor BB, patch = CC*100 + DD. `to_string` prints patch/100.
- file major > app major → info message ("lower version").
- file > app AND (minor differs OR patch/100 differs) → `Newer3mfVersionDialog`.
- Loaded config empty → warning "not from Bambu Lab"/"old Bambu Studio".
- file < 1.5.9 → `translate_old` key migration. File < 2.0.0 → prime-tower migration (Plater.cpp:12024). **Use a version ≥ 2.0.0** so the converter's values are not migrated again.
- `!is_bbl_vendor_config(printer_model) && !check_project_config` → config not loaded. `check_project_config` fails when there is more than one `nozzle_diameter` value and the `extruder_type` size differs.
- `config.apply(FullPrintConfig::defaults()); config += loaded` (11985). Missing keys get defaults.
- `load_config_file_config` **throws when `filament_colour` is missing** (PresetBundle.cpp:5285-5290). Always write `filament_colour`.
- Bambu master SLIC3R_VERSION = "02.09.01.70".

### 2.4 Orca Plater (Orca Plater.cpp:9465-9590)
- App version = SoftFever_VERSION (e.g. "2.6.0-dev"). Orca **writes** `Application=BambuStudio-<"02.08.01.55">` plus `<metadata name="OrcaSlicer">2.x.y</metadata>` (O:7255-7330).
- With the OrcaSlicer tag (`From_Orca`): the tag is compared to the Orca app version. Newer and minor differs → dialog. Versions below 2.3.1-alpha get migration.
- Without the tag (`From_BBS`): version > 2.3.2 → treated as a Bambu project, config loaded (unknown keys only logged). Version ≤ 2.3.2 → old/compatible path, still loads.
- `From_Prusa` → geometry only.

### 2.5 Recommendation
- Bambu target: `Application = BambuStudio-02.01.00.00`. This is ≥ 2.0.0 (no migration), major 2 is not above the app (no dialog in Bambu 2.x), and it is lower than current Bambu. Omit the OrcaSlicer tag.
- Orca target: `Application = BambuStudio-02.01.00.00` plus `<metadata name="OrcaSlicer">2.3.0</metadata>`, or the lowest Orca version whose keys the converter emits. Without the tag Orca still loads config.
- Always: `BambuStudio:3mfVersion = 1`.

---

## 3. `Metadata/model_settings.config`

### 3.1 Shape (writer B:8612-8980, reader B:4913-5526)
```xml
<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="3">                                 <!-- = root-model object id -->
    <metadata key="name" value="Cube"/>
    <metadata key="extruder" value="1"/>          <!-- per-object config keys, values as serialized strings -->
    <metadata key="layer_height" value="0.2"/>
    <part id="1" subtype="normal_part">           <!-- id = sub-model object id (volume id) -->
      <metadata key="name" value="Cube"/>
      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>
      <metadata key="source_file" value="cube.stl"/>
      <metadata key="source_object_id" value="0"/>
      <metadata key="source_volume_id" value="0"/>
      <metadata key="source_offset_x" value="0"/>
      <metadata key="source_offset_y" value="0"/>
      <metadata key="source_offset_z" value="0"/>
      <metadata key="extruder" value="2"/>        <!-- per-volume config -->
      <mesh_stat face_count="12" edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>
    </part>
    <part id="2" subtype="modifier_part"> ... </part>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="plater_name" value=""/>
    <metadata key="locked" value="false"/>
    <metadata key="filament_map_mode" value="Auto For Flush"/>   <!-- Bambu 2.x; optional -->
    <metadata key="thumbnail_file" value="Metadata/plate_1.png"/> <!-- optional -->
    <model_instance>
      <metadata key="object_id" value="3"/>
      <metadata key="instance_id" value="0"/>
      <metadata key="identify_id" value="123"/>
    </model_instance>
  </plate>
  <assemble>
    <assemble_item object_id="3" instance_id="0" transform="1 0 0 0 1 0 0 0 1 128 128 10" offset="0 0 0"/>
  </assemble>
</config>
```

### 3.2 Object-level keys
- `name` is special. **Every other `<metadata key>` becomes per-object config** (an opt_key with a string value).
- Bambu also writes `<metadata key="module">` and a bogus `<metadata face_count="N"/>` (no key). Both are ignored on read.
- If there is no `<object>` entry, the name comes from the root `<object name>` (B:~2620).

### 3.3 Part-level keys (B:5619-5874)
| key | meaning |
|---|---|
| `name` | volume name |
| `volume_type` / attribute `subtype` | `normal_part`, `negative_part`, `modifier_part`, `support_enforcer`, `support_blocker`. Unknown values become normal_part (Model.cpp:3665-3680) |
| `matrix` | **stored only into `source.transform`** (reload-from-disk). Does NOT position the volume. 16 numbers, row-major 4x4 = volume_matrix * source.transform |
| `source_file`, `source_object_id`, `source_volume_id`, `source_offset_x/y/z`, `source_in_inches`, `source_in_meters` | source info |
| `uuid`, `assembly_src_guid` | ignored/kept |
| anything else | per-volume config |
| `<text_info .../>` child | Bambu text (§8) |
| `<BambuStudioShape>`/`<slic3rpe:shape>` + `<slic3rpe:text>` | emboss text (§8) |
| `<mesh_stat>` | informational |

### 3.4 Volume positioning on import (the gotcha)
B:5619-5874 calls `add_volume(mesh, MODEL_PART, modify_to_center_geometry)`. `modify_to_center_geometry` is false when `text_info` is non-empty (Bambu text) and **true otherwise**. When true, the mesh is translated so its bbox center sits at the origin. Then `volume transform = component_transform * T(center)`, so the world position is preserved. **Orca always recenters** (O:~5350).
→ The converter can write any local frame. Geometry is preserved. The only frame that matters is for text (§8).

After load (B:2650-2700):
- A single-volume object gets its volume offset moved into the instance.
- Extruder fixup (B:2668-2694): an object `extruder` of 0 or missing becomes 1. Single-volume objects get their **volume** `extruder` erased (they use the object's). A volume extruder > filament count (taken from the size of project `filament_settings_id`) becomes 1.
  → For single-volume objects, put the extruder on the **object**.

### 3.5 Plates (B:4913-5175 read; PartPlate.cpp)
Plate metadata keys: `plater_id` (1-based), `plater_name`, `locked` (`true`/`false`), `bed_type` (§9), `print_sequence` (`by layer`/`by object`), `first_layer_print_sequence`, `other_layers_print_sequence`, `other_layers_print_sequence_nums`, `spiral_mode`, `filament_map_mode`, `filament_maps`, `filament_volume_maps`, `gcode_file`, `thumbnail_file`, `thumbnail_no_light_file`, `top_file`, `pick_file`, `pattern_bbox_file`. Unset per-plate options inherit from the project.

`model_instance`: `object_id` (root object id), `instance_id` (0-based within the object), `identify_id` (any unique int).
- obj_inst_map is a `std::map` keyed by object_id, so **only one instance per object per plate is kept** (B:5341-5372).
- **Real plate membership is recomputed geometrically** by `reload_all_objects` (bbox intersection with the plate's rectangle; PartPlate.cpp:6205-6280). The plate list mainly creates the plates (count, names, settings; `load_from_3mf_structure` PartPlate.cpp:7573-7770).
→ **Instances must be placed in world coordinates inside their plate's rectangle** (§3.6). Emit one `<plate>` per target plate, including empty ones.

### 3.6 Plate grid (PartPlate.cpp; Orca identical: Orca PartPlate.cpp:6980, 5983, .hpp:62)
```
LOGICAL_PART_PLATE_GAP = 1/5                               (PartPlate.cpp:63)
plate_w, plate_d = int(printable bbox size)                (Plater.cpp:21780 reset_size(int,int,...); bbox from printable_area,
                                                            3DBed calc_printable_bounding_box +-DefaultTipRadius 1.25 only on display)
stride_x = plate_w * (1 + 1/5);  stride_y = plate_d * (1 + 1/5)        (PartPlate.cpp:5764-5778)
cols = compute_colum_count(n_plates)                        (PartPlate.hpp:38-50)
       = r=round(sqrtf(n)); sqrtf(n) > r ? r+1 : r   == ceil(sqrt(n)): 1→1,2→2,3→2,4→2,5→3,...,9→3,10→4
       (verified verbatim, Bambu PartPlate.hpp:38-50 / Orca PartPlate.hpp:62-74; n = total plate count)
row = i / cols;  col = i % cols           (i = 0-based plate index)
origin(i) = (col * stride_x, -row * stride_y)   (PartPlate.cpp:4860-4872)   rows grow toward -Y
```
World position of an instance on plate i = plate-local position + origin(i). The printable_area origin offset (usually 0,0) is added as well. REF reader.py:42-55 has an inverse helper.

### 3.7 `<assemble>` (B:5386-5427)
Each `assemble_item` has `object_id`, `instance_id`, `transform` (12 numbers), `offset` (3 numbers), and optional `volume_id`. **The object_id must exist** or the load errors. The section is optional. If it is emitted, mirror the build transforms with offset `0 0 0`.

---

## 4. Painting bitstreams

### 4.1 Common encoding (TriangleSelector serialize; identical in Prusa/Bambu/Orca for states 0..16)
- The string is uppercase hex. It is read **from the end backward**: each hex char is a nibble, LSB first.
- Per node: 2 bits for split count (0 = leaf, 1-3 = split into 2-4 children; then 2 bits special side for splits, then children recursively in reverse).
- Leaf state:
  - states 0-2: 2 bits (`00`,`01`,`10`)
  - states 3-16: `11` + nibble(n-3), where nibble 0..13
  - states ≥17: **implementation-specific** (below)
- **Lowercase hex**: Bambu drops that triangle's paint (Model.cpp:4704-4734). Always use uppercase.

| impl | encoding of state n ≥ 17 | Cite |
|---|---|---|
| Bambu | `11` then repeated nibble `1111` (adds 15 each) then remainder nibble | TriangleSelector.cpp:2036-2110 |
| Orca | n ≤ 17 → nibble n-3 (n=17 → `1110`); n ≥ 18 → `1111` + nibble(n-18). Same as Bambu for n ≤ 32 | Orca TriangleSelector.cpp:1730-1790 |
| Prusa 2.9 (painting version 2) | n ≥ 17 → `11` `1110` + 8 bits (n-17) | P:71-78 |

**Gotcha:** Bambu and Orca read Prusa's `1110` as state 17 and then misparse the next 8 bits, which corrupts the rest of that triangle. So a **converter must decode Prusa strings and re-encode them** whenever any state ≥ 17 occurs (in practice only `mmu_segmentation` with more than 16 extruders). Pass the string through when every state is ≤ 16 (the strings are bit-identical).

Max state: Orca ExtruderMax = 32; Bambu MAX_FILAMENT_COUNT.

### 4.2 Attribute mapping
| Prusa (`slic3rpe:` on `<triangle>`) | Bambu/Orca | States |
|---|---|---|
| `slic3rpe:custom_supports` | `paint_supports` | 1 = enforcer, 2 = blocker |
| `slic3rpe:custom_seam` | `paint_seam` | 1 = enforcer, 2 = blocker |
| `slic3rpe:mmu_segmentation` | `paint_color` | 0 = volume extruder, N = filament N |
| `slic3rpe:fuzzy_skin` (2.8+) | `paint_fuzzy_skin` (Bambu, Orca recent) | 1 = fuzzy |

- **Triangle order**: Prusa stores **one concatenated mesh per object** (§7). Paint strings go per triangle in that order. Split them per volume using the volume `firstid`/`lastid` (triangle index ranges).
- Prusa reverses the triangle winding for left-handed (mirrored) volumes (P:~3200). After unbaking, keep the triangles exactly as Prusa wrote them (the indices are what the paint refers to), but see §7.3.

---

## 5. Custom G-code and layer files

### 5.1 Custom gcode per layer
Prusa (P:3865-3925 write, ~1100 read). This file has **multiple root elements**, which boost ptree allows. Parse it leniently, e.g. wrap it in a dummy root:
```xml
<custom_gcodes_per_print_z bed_idx="0">
  <code print_z="2.4" type="0" extruder="1" color="#FF0000" extra="" gcode="M600"/>
  <mode value="SingleExtruder"/>
</custom_gcodes_per_print_z>
<custom_gcodes_per_print_z bed_idx="1"> ... </custom_gcodes_per_print_z>
```
File: `Metadata/Prusa_Slicer_custom_gcode_per_print_z.xml`. Bed 0 is always written. The mode comes from bed 0.

Bambu `Metadata/custom_gcode_per_layer.xml` (B:9381-9436 write, 3821-3905 read):
```xml
<?xml version="1.0" encoding="utf-8"?>
<custom_gcodes_per_layer>
  <plate>
    <plate_info id="1"/>                                   <!-- 1-based plate -->
    <layer top_z="2.4" type="0" extruder="1" color="#FF0000" extra="" gcode="M600"/>
    <mode value="SingleExtruder"/>                         <!-- SingleExtruder | MultiAsSingle | MultiExtruder -->
  </plate>
</custom_gcodes_per_layer>
```
- `type`: ColorChange=0, PausePrint=1, ToolChange=2, Template=3, Custom=4, Unknown=5. Same in both.
- Mapping: `bed_idx` k becomes `plate_info id` k+1, and `print_z` becomes `top_z`.
- Bambu loads this only when the config is loaded (§2.2).

### 5.2 Layer height profile
Prusa `Metadata/Slic3r_PE_layer_heights_profile.txt` → Bambu `Metadata/layer_heights_profile.txt`. Same line format:
```
object_id=1|0.000000;0.200000;0.200000;0.200000;...
```
- `object_id` = **1-based ModelObject index in file order**, NOT the 3MF object id (B:2506-2520, 8249-8282).
- Values are `z;h` pairs. Bambu requires more than 4 values and an even count (B:3328-3388).
- The REF reader.py wrongly keys these by object id; its writer maps by index.

### 5.3 Layer config ranges
Prusa `Metadata/Prusa_Slicer_layer_config_ranges.xml` → Bambu `Metadata/layer_config_ranges.xml`. Same shape:
```xml
<objects>
  <object id="1">                                <!-- 1-based object index -->
    <range min_z="0" max_z="2">
      <option opt_key="extruder">2</option>
      <option opt_key="layer_height">0.1</option>
    </range>
  </object>
</objects>
```
(B:2597, ~8285-8340.) The option keys need Prusa→Bambu key mapping, as for any config.

### 5.4 Cut information
Bambu `Metadata/cut_information.xml` (B:8983-9045):
```xml
<objects>
  <object id="1">                                <!-- 1-based object index -->
    <cut_id id="..." check_sum="1" connectors_cnt="0"/>
    <connectors><connector volume_id="0" type="0" radius="0" height="0" r_tolerance="0" h_tolerance="0"/></connectors>
  </object>
</objects>
```
Prusa `Metadata/Prusa_Slicer_cut_information.xml` has the same shape without radius/height (P:3440-3495). Connector type enum differs: Prusa {Plug, Dowel, Snap, Undef}; Bambu {Plug, Dowel, Snap, Thread, Undef}, so Undef shifts. Safe option: drop cut info. It only links cut parts.

---

## 6. Wipe tower

Prusa 2.9 `Metadata/Prusa_Slicer_wipe_tower_information.xml` (P:3928-3960 write, ~1170 read). Multiple root elements:
```xml
<wipe_tower_information bed_idx="0" position_x="180" position_y="140" rotation_deg="0"/>
<wipe_tower_information bed_idx="1" position_x="..." position_y="..." rotation_deg="0"/>
```
Positions are **bed-local** (per bed). Legacy fallback: `Slic3r_PE.config` keys `wipe_tower_x`/`wipe_tower_y`/`wipe_tower_rotation_angle`, default 180/140.

Bambu/Orca: `project_settings.config` keys
- `wipe_tower_x`, `wipe_tower_y`: **coFloats, one entry per plate**, plate-local (world − plate origin), default `15`. Read per plate index (Bambu PrintConfig.cpp:7054-7062; PartPlate.cpp:5195-5221).
- `wipe_tower_rotation_angle`: scalar (Orca PrintConfig.cpp:8326-8352). Use bed 0's value.

```json
"wipe_tower_x": ["180", "165.2"], "wipe_tower_y": ["140", "200"]
```
The array length must be ≥ the plate count, else missing plates get the default. Prusa and Bambu both measure the position at the tower's min corner in bed coords, so pass the numbers through per bed. Beware a different bed size/origin (Prusa has no plate-origin offset per bed; it is local). Clamp into the printable area.

---

## 7. PrusaSlicer 2.9 input side

### 7.1 Entries
| Entry | Content | Cite |
|---|---|---|
| `3D/3dmodel.model` | single model file, `xmlns:slic3rpe="http://schemas.slic3r.org/3mf/2017/06"`, metadata `slic3rpe:Version3mf`=1, `slic3rpe:MmPaintingVersion`, `slic3rpe:FdmSupportsPaintingVersion`, `slic3rpe:SeamPaintingVersion`, `Application=PrusaSlicer-2.9.x` | P:3055-3420 |
| `Metadata/Slic3r_PE.config` | `; key = value` lines (full config) | P:~3500 |
| `Metadata/Slic3r_PE_model.config` | objects/volumes | P:3727-3840 |
| `Metadata/Slic3r_PE_layer_heights_profile.txt`, `Prusa_Slicer_layer_config_ranges.xml`, `Prusa_Slicer_sla_*.txt`, `Prusa_Slicer_custom_gcode_per_print_z.xml`, `Prusa_Slicer_cut_information.xml`, `Prusa_Slicer_wipe_tower_information.xml` | see §5/§6 | |
| `Metadata/thumbnail.png` (+ `Metadata/thumbnail_*`) | thumbnail | |
| `3D/_rels/...` | **absent** | |

### 7.2 Geometry
- **One `<object>` per ModelObject**, with all volumes concatenated in one `<mesh>`. Vertices are **BAKED** with the volume matrix, so they are in object space (P:3150-3250).
- Volumes that are left-handed (det < 0) have their triangle winding flipped on write (P:~3200).
- Additional instances of the same object become **separate `<object>`s** containing `<components><component objectid="<first object id>"/></components>`. One `<build><item objectid transform printable>` per instance (P:3300-3420).
- `Slic3r_PE_model.config`:
```xml
<config>
 <object id="1" instances_count="2">
  <metadata type="object" key="name" value="Cube"/>
  <metadata type="object" key="extruder" value="1"/>
  <volume firstid="0" lastid="11">              <!-- triangle index range in the concatenated mesh -->
   <metadata type="volume" key="name" value="Cube"/>
   <metadata type="volume" key="volume_type" value="ModelPart"/>   <!-- ModelPart|NegativeVolume|ParameterModifier|SupportEnforcer|SupportBlocker -->
   <metadata type="volume" key="modifier" value="0"/>
   <metadata type="volume" key="matrix" value="16 numbers"/>
   <metadata type="volume" key="source_file" .../> ... source_object_id, source_volume_id, source_offset_x/y/z, source_in_inches ...
   <metadata type="volume" key="extruder" value="2"/>               <!-- per-volume config -->
   <slic3rpe:shape .../><slic3rpe:text .../>                          <!-- text, see §8 -->
   <mesh edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>
  </volume>
 </object>
</config>
```
(P:3727-3840; REF VOLUME_TYPES.)

### 7.3 Unbaking
- Prusa import (P:2576-2700) **recenters the baked mesh**. `matrix` is only `source.transform`, used for reload-from-disk. It is **not reliable** for unbaking: it can be identity after ops or stale after scaling.
- Recommended: take the vertices of triangles `firstid..lastid`, reindex them into a per-volume mesh, and keep them in **object space**. Write component transform = identity (or T(c) with mesh translated by −c). Bambu/Orca recenter on load anyway (§3.4). Geometry is exact.
- Since vertices stay in the same space, the winding flip for left-handed volumes has already produced correct outward normals in object space. Keep the triangles as they are.
- Exception: text volumes need a proper local frame (§8).
- Map `volume_type`: ModelPart→normal_part, NegativeVolume→negative_part, ParameterModifier→modifier_part, SupportEnforcer→support_enforcer, SupportBlocker→support_blocker. If `volume_type` is missing, `modifier=1` → modifier_part.

### 7.4 Multiple beds (2.9) → plates (MB:30-130, 360-435, 440-450)
- Bed translation(k) = (x*(W+gap), y*(D+gap)), with `gap = min(100, 0.3 * bed diagonal)` (W, D = bed bbox size).
- Index → (x, y) follows a spiral grid: 0(0,0) 1(1,0) 2(0,1) 3(1,1) 4(2,0) 5(2,1) 6(0,2) 7(1,2) 8(2,2) ... (square shells; rows grow toward **+Y**).
- Legacy (pre-2.9 multibed) layout is linear along +X with gap 0.2*W.
- **Bed membership = instance position**. There is no explicit list: find the k whose translated bed bbox contains the instance's bbox center (MB:440-450).
- Conversion: local = world − bed_translation(k); Bambu world = local + plate_origin(k) (§3.6). Note the opposite Y direction.

---

## 8. Text mapping

### 8.1 Representations
| | Prusa / Orca emboss | Bambu `text_info` |
|---|---|---|
| element | `<slic3rpe:shape>` + `<slic3rpe:text>` (Orca also `BambuStudioShape`) | `<text_info .../>` child of `<part>` |
| detection | Orca `is_text = text_configuration.has_value()` | Bambu `is_text = !m_text_info.m_text.empty()` (Model.hpp:1165) |
| mesh frame | text local frame; fix matrix in shape `transform` | mesh NOT recentered on load (`modify_to_center_geometry=false`) |
| Orca reads text_info? | **no**, legacy text_info is ignored (O:5061-5110) | |
| Bambu reads emboss? | parses `BambuStudioShape`/`slic3rpe:shape` (B:275-292, 414-451); its text gizmo uses text_info | |

### 8.2 Prusa/Orca `slic3rpe:text` attributes (O:9930-9971; P same)
`text`, `style_name`, `font_descriptor`, `font_descriptor_type` (`file_name` | `wxFontDescriptor_Windows` | `wxFontDescriptor_Linux` | `wxFontDescriptor_MacOsX` | `undefined`), `char_gap`, `line_gap`, `line_height` (= size in mm), `boldness`, `skew`, `per_glyph`, `horizontal` (left/center/right), `vertical` (top/middle/bottom), `collection`, `family`, `face_name`, `style`, `weight`.
`slic3rpe:shape`: `filepath`, `filepath3mf`, `scale`, `unhealed`, `depth`, `use_surface`, `transform` (the fix matrix, 16 numbers). Orca writes shape then text (O:8416-8424). Text without a shape → `read_old`.

### 8.3 Frame math
- Prusa writes a baked mesh B (object space). `fix = V^-1 * T(center(B))`, where V = volume matrix (P:~2650; P fix_3mf_tr).
- Recover: `V = T(center(B)) * fix^-1`. Local mesh `L = V^-1 * B`.
- Orca `create_fix` uses identity, giving `T(center(L))` (O:10060-10095). The emboss gizmo uses `volume_matrix * fix^-1` (orca_GLGizmoEmboss.cpp:494, 850, 1224).
- **Orca output:** sub-model mesh = L, component transform = V, shape `transform` = T(center_bbox(L)), text config copied 1:1 (shape + text elements inside `<part>`).
- **Bambu output:** `<text_info>` with `font_version="2.8"` (CUR_FONT_VERSION, GLGizmoText.hpp:32). Mesh = L, not recentered, component = V. Bambu regenerates the mesh on gizmo open only if the text differs. Unverified: z-convention of Bambu text local frame vs. Prusa (text base at z=0 vs centered on depth). Expect a possible depth/2 offset only after user regeneration.

### 8.4 Bambu `text_info` attributes (read B:5434-5526, write B:8541-8598; TextInfo Model.hpp:870-899)
```xml
<text_info text="Hello" font_name="Arial" font_version="2.8" style_name="" boldness="0" skew="0"
  font_index="0" font_size="10" thickness="2" embeded_depth="0" rotate_angle="0" text_gap="0" line_gap="0"
  horizontal="center" vertical="middle" bold="1" italic="0" surface_type="1"
  hit_mesh="-1" hit_position="0 0 0" hit_normal="0 0 1"/>
```
- `boldness`/`skew` are read only if font_version > 2.2. `surface_type` is written when font_version ≥ 2.0. Older files use `surface_text` + `keep_horizontal`. font_version < 2.0 is treated as "old" (GLGizmoText.cpp:3878-3890).
- Defaults: font_size 10, bold true, thickness 2, surface_type 1.
- TextType: HORIZONAL=0, SURFACE=1, SURFACE_HORIZONAL=2, SURFACE_CHAR=3.
- `horizontal` accepts left/center/right or 0/1/2. `vertical` accepts top/middle/bottom.

### 8.5 Field mapping Prusa → Bambu
| Bambu | from Prusa |
|---|---|
| text | text |
| font_name | face_name (fall back to family) |
| style_name | style_name |
| font_size | line_height |
| thickness | shape depth (projection depth) |
| embeded_depth | 0 |
| text_gap | char_gap |
| line_gap | line_gap |
| bold | weight == bold (or boldness > 0) |
| italic | style == italic (or skew ≠ 0) |
| boldness, skew | same |
| surface_type | per_glyph → 3; use_surface → 1; else 0 |
| horizontal, vertical | same strings |
| hit_mesh | -1 |

REF inverse `_text_config_attrs` (writer.py:603-653) confirms this mapping in reverse: depth = thickness + embeded_depth; surface_type 1/2/3 → use_surface, 3 → per_glyph; char_gap = round(text_gap); line_height = font_size; Windows descriptor `1;10;-13;0;0;0;{700|400};{1|0};0;0;1;0;0;2;0;{face}`.

---

## 9. Other gotchas

1. **Emit `3D/_rels/3dmodel.model.rels`** or Bambu routes the file to the Prusa loader whenever "PrusaSlicer" appears in the Application string (Bambu 3mf.cpp:286-345). Never write "PrusaSlicer" in Application.
2. **Application must start with `BambuStudio-`** for Bambu to load project_settings, custom gcode and the other config files (B:2259-2271).
3. **`filament_colour` must be present** in project_settings or the config load throws (PresetBundle.cpp:5285-5290). All per-filament vectors (`filament_settings_id`, `filament_colour`, `filament_type`, ...) need the same length = filament count. The volume-extruder clamp uses the size of `filament_settings_id` (B:2668-2694).
4. **project_settings.config is JSON**: `{"version": "...", "name": "project_settings", "from": "project", key: "str" | ["str", ...]}`. All scalars are strings; vectors are arrays of strings (Config.cpp:1583-1632, 885+). Also include `print_settings_id`, `printer_settings_id`, `printer_model`, and `inherits` where known. A non-BBL `printer_model` plus a nozzle/extruder_type count mismatch → config ignored (Plater.cpp:11796-11960).
5. **Component `p:path` with leading "/"**, matching the rels Target exactly (B:4481-4503).
6. **Index-keyed side files**: layer heights, ranges, cut info and brim use the **1-based ModelObject index** (order of `<object>` entries in model_settings / build), not the 3MF id (B:2506-2520, 2597).
7. **Plates are geometric**: plate membership comes from world position (§3.6). Prusa bed membership is also geometric with a different layout (§7.4). Re-place every instance.
8. **One instance per object per plate** is recorded in model_settings (B:5341-5372). Harmless because of geometric recompute, but don't rely on the list.
9. **Painting states ≥ 17**: re-encode (§4). Uppercase hex only.
10. **Prusa XML side files with multiple roots** (custom gcode, wipe tower): wrap them before parsing.
11. **Extruder on single-volume objects goes on the object**. Volume extruders there are erased (B:2668-2694). 0 means "inherit", mapped to 1 for objects.
12. **Per-plate wipe tower** arrays sized to plate count (§6).
13. **Bed types** (`bed_type` / `curr_bed_type`): Bambu `Default Plate`, `Cool Plate`, `Engineering Plate`, `High Temp Plate`, `Textured PEI Plate`, `Supertack Plate`; Orca adds `Textured Cool Plate` (PrintConfig.cpp enum maps).
14. **print_sequence** values: `by layer`, `by object` (Prusa `complete_objects=1` → `by object`).
15. **Thumbnails** are optional. If emitted, store them uncompressed (B:6826-6900).
16. **slice_info.config** (optional; Bambu always writes it):
```xml
<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-BBL-Client-Version" value="02.01.00.00"/>
  </header>
</config>
```
(B:9046-9120)
17. **filament_sequence.json** is optional: `{"plate_1":{"sequence":[],"nozzle_sequence":[],"optimal_assignment":[]}}` (B:3718-3767, 9499-9537).
18. **Object/volume names**: always write `name` in model_settings. Otherwise the name falls back to the root `<object name>`.
19. **Orca-only keys** (e.g. `paint_texture_*`, Orca config keys) are logged-and-ignored by Bambu. Unknown config keys are dropped with a log in both.
20. **Number formatting**: `%.9g` for vertices. Transforms use full precision.
