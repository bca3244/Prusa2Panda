# Web version: progress note

Running note for the GitHub Pages build of Prusa2Panda (branch `claude/project-thread-0tg4ru`),
so the work can resume after an interruption. Delete this file once the web version is merged.

## Plan

- [ ] Core: `convert_file(install=...)` and a table-backed install (`prusa2panda/preset_table.py`)
- [ ] `tools/gen_preset_table.py`: preset table from BambuStudio / OrcaSlicer `resources/profiles`
- [ ] `web/`: static page, runs the core in Pyodide (+lxml), downloads the result
- [ ] `tools/build_web.py`: assembles `_site/` (page, package zip, preset tables)
- [ ] `.github/workflows/pages.yml`: build, headless-browser smoke conversion, deploy to Pages
- [ ] Tests, README section, CHANGELOG
- [ ] Fable review, PR, CI green, merge

## Decisions

- Pyodide 0.29.5 (Python 3.13, lxml 6.0.2) from the jsDelivr CDN.
- Presets referenced by exact system preset name only (verified in the feasibility test); the
  table is regenerated from the latest slicer releases on every deploy and weekly.
- Project header version uses the converter defaults (not the table's slicer version), so files
  open in older slicer builds too.

## Log
