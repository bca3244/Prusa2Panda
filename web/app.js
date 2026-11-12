// Prusa2Panda web version: the page. The conversion itself runs in worker.js (Pyodide).
"use strict";

const $ = (id) => document.getElementById(id);
const PRUSA = "__prusa__";
const FILE = "__file__";
const DEFAULT_PRINTERS = {
  bambu: ["Bambu Lab P1S 0.4 nozzle", "Bambu Lab X1 Carbon 0.4 nozzle"],
  orca: ["Bambu Lab P1S 0.4 nozzle", "Bambu Lab X1 Carbon 0.4 nozzle"],
};
const DEFAULT_VENDOR = { orca: "BBL", bambu: "BBL" };
const SLICER_NAMES = { bambu: "Bambu Studio", orca: "OrcaSlicer" };
// Same as target.normalized_type in the converter.
const GENERIC_TYPE = {
  PLA: "PLA", PETG: "PETG", PCTG: "PETG", PET: "PETG", ABS: "ABS", ASA: "ASA", FLEX: "TPU", TPU: "TPU",
  PC: "PC", PA: "PA", NYLON: "PA", PVA: "PVA", HIPS: "HIPS", PP: "PP", PVB: "PVB",
};

const state = {
  build: null,          // build-info.json
  indexes: {},          // slicer -> presets/<slicer>/index.json
  tables: {},           // "slicer/file" -> {json, text}
  project: null,        // inspect() result
  fileName: "",
  printerFile: null,    // {path, printer, vendor: index entry or null, name}
  busy: false,
};

// -- preferences (per browser; the page works without them) ------------------------------------
function loadPrefs() {
  try { return JSON.parse(localStorage.getItem("p2p.prefs") || "{}") || {}; } catch (e) { return {}; }
}
function savePrefs(patch) {
  try { localStorage.setItem("p2p.prefs", JSON.stringify(Object.assign(loadPrefs(), patch))); } catch (e) { /* ignore */ }
}

// -- worker -----------------------------------------------------------------------------------
const worker = new Worker("worker.js");
let nextId = 1;
const pending = new Map();
worker.onmessage = (ev) => {
  const m = ev.data;
  if (m.status) { setBoot(m.status); return; }
  const p = pending.get(m.id);
  if (!p) return;
  pending.delete(m.id);
  if (m.ok) p.resolve(m.result);
  else { const e = new Error(m.error); e.detail = m.detail; p.reject(e); }
};
worker.onerror = (ev) => {
  bootFailed("The converter could not start: " + (ev.message || "worker error"));
};
function call(cmd, payload = {}, transfer = []) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker.postMessage(Object.assign({ id, cmd }, payload), transfer);
  });
}

let pythonReady = false;
function setBoot(text) { $("boot-text").textContent = text; }
function bootFailed(text) {
  const b = $("boot");
  b.className = "boot err";
  b.textContent = text;
}

// -- helpers ----------------------------------------------------------------------------------
function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c !== null && c !== undefined) e.append(c);
  return e;
}
function first(v) { return Array.isArray(v) ? (v.length ? String(v[0]) : "") : (v == null ? "" : String(v)); }
function normalizedType(t) {
  t = String(t || "").trim().toUpperCase();
  return GENERIC_TYPE[t] || (t ? t.split("-")[0] : "");
}
function compatible(cfg, printer) {
  const cp = cfg.compatible_printers;
  return !cp || !cp.length || cp.includes(printer);
}
function slicer() { return document.querySelector("input[name=slicer]:checked").value; }
function settingsMode() { return document.querySelector("input[name=settings]:checked").value; }
function vendorEntry() {
  const idx = state.indexes[slicer()];
  return idx ? idx.vendors.find((v) => v.name === $("vendor").value) || idx.vendors[0] : null;
}
function currentTable() {
  const v = vendorEntry();
  const t = v && state.tables[slicer() + "/" + v.file];
  return t ? t.json : null;
}

async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r;
}
async function loadIndex(s) {
  if (!state.indexes[s]) state.indexes[s] = await (await fetchJson(`presets/${s}/index.json`)).json();
  return state.indexes[s];
}
async function loadTable(s, entry) {
  const key = s + "/" + entry.file;
  if (!state.tables[key]) {
    const text = await (await fetchJson(`presets/${s}/${encodeURIComponent(entry.file)}`)).text();
    state.tables[key] = { json: JSON.parse(text), text, file: entry.file, slicer: s };
  }
  return state.tables[key];
}

// -- target: vendor / printer / process -------------------------------------------------------
async function onSlicerChange() {
  const s = slicer();
  savePrefs({ slicer: s });
  const idx = await loadIndex(s);
  $("profile-version").textContent =
    `Presets of ${SLICER_NAMES[s]} ${idx.version}. Older and newer ${SLICER_NAMES[s]} versions open the file too; ` +
    "a preset your version does not have opens as a project preset.";
  const vsel = $("vendor");
  vsel.replaceChildren(...idx.vendors.map((v) => el("option", { value: v.name, text: v.title || v.name })));
  const pref = (loadPrefs().vendor || {})[s];
  vsel.value = idx.vendors.some((v) => v.name === pref) ? pref
    : (idx.vendors.some((v) => v.name === DEFAULT_VENDOR[s]) ? DEFAULT_VENDOR[s] : idx.vendors[0].name);
  $("vendor-field").hidden = idx.vendors.length < 2;
  state.printerFile = null;
  $("printer-file").value = "";
  $("printer-file-info").textContent = $("printer-file-info").dataset.text;
  await onVendorChange();
}

async function onVendorChange() {
  const s = slicer();
  const entry = vendorEntry();
  const prefs = loadPrefs();
  savePrefs({ vendor: Object.assign({}, prefs.vendor, { [s]: entry.name }) });
  await loadTable(s, entry);
  const psel = $("printer");
  const groups = Object.entries(entry.models).map(([model, names]) =>
    el("optgroup", { label: model || entry.title || entry.name },
      ...names.map((n) => el("option", { value: n, text: n }))));
  groups.push(el("optgroup", { label: "Other" },
    el("option", { value: PRUSA, text: "PrusaSlicer printer as-is (a custom printer; switch it after opening)" }),
    el("option", { value: FILE, text: `From a ${SLICER_NAMES[s]} .3mf / exported .json preset…` })));
  psel.replaceChildren(...groups);
  const want = [(prefs.printer || {})[s], ...DEFAULT_PRINTERS[s]];
  const all = new Set([...entry.printers, PRUSA, FILE]);
  psel.value = want.find((n) => n && all.has(n)) || entry.printers[0];
  onPrinterChange();
}

function printerDefaults(table, printer) {
  const m = table.machine[printer] || {};
  const procs = Object.keys(table.process).filter((n) => compatible(table.process[n], printer)).sort();
  const fils = Object.keys(table.filament).filter((n) => compatible(table.filament[n], printer)).sort();
  let proc = String(m.default_print_profile || "");
  if (!procs.includes(proc)) proc = procs.find((n) => /0\.20mm Standard/.test(n)) || procs[0] || "";
  let fil = first(m.default_filament_profile);
  if (!fils.includes(fil)) fil = fils.find((n) => /^Generic PLA/.test(n)) || fils[0] || "";
  return { procs, fils, proc, fil };
}

function onPrinterChange() {
  const s = slicer();
  const p = $("printer").value;
  const prefs = loadPrefs();
  savePrefs({ printer: Object.assign({}, prefs.printer, { [s]: p }) });
  $("printer-file-field").hidden = p !== FILE;
  const table = currentTable();
  const isPreset = p !== PRUSA && p !== FILE;
  $("process-field").hidden = !isPreset;
  if (isPreset && table) {
    const d = printerDefaults(table, p);
    const sel = $("process");
    sel.replaceChildren(...d.procs.map((n) => el("option", { value: n, text: n })));
    const pref = (prefs.process || {})[s + "|" + p];
    sel.value = d.procs.includes(pref) ? pref : d.proc;
  }
  renderFilaments();
  updateButton();
}

async function onPrinterFile() {
  const f = $("printer-file").files[0];
  const info = $("printer-file-info");
  state.printerFile = null;
  if (!f) { updateButton(); return; }
  info.textContent = "Reading…";
  try {
    const bytes = await f.arrayBuffer();
    const r = await call("preset_file", { name: f.name, bytes }, [bytes]);
    const idx = await loadIndex(slicer());
    const vendor = idx.vendors.find((v) => v.printers.includes(r.printer)) || null;
    if (vendor) await loadTable(slicer(), vendor);
    state.printerFile = { path: r.path, printer: r.printer, vendor, name: f.name };
    info.textContent = vendor
      ? `Printer “${r.printer}” (a ${SLICER_NAMES[slicer()]} system preset).`
      : `Printer “${r.printer}” is not a ${SLICER_NAMES[slicer()]} system preset: it is carried along as a project preset.`;
  } catch (e) {
    info.textContent = "Could not read that file: " + e.message;
  }
  updateButton();
}

// -- project ----------------------------------------------------------------------------------
async function onFile(file) {
  if (!file) return;
  if (!pythonReady) {
    $("drop-text").textContent = "Still starting the converter… try again in a moment.";
    return;
  }
  state.project = null;
  state.fileName = file.name;
  $("drop-text").textContent = `Reading ${file.name}…`;
  $("result").replaceChildren();
  updateButton();
  try {
    const bytes = await file.arrayBuffer();
    state.project = await call("inspect", { name: file.name, bytes }, [bytes]);
    $("drop-text").replaceChildren(el("strong", { text: file.name }), " · choose another file");
    renderInfo();
    renderBeds();
    renderFilaments();
  } catch (e) {
    $("drop-text").textContent = `Could not read ${file.name}: ${e.message}`;
    $("info").hidden = true;
    $("beds-row").hidden = true;
    $("fil-card").hidden = true;
  }
  updateButton();
}

function renderInfo() {
  const p = state.project;
  const dl = el("dl", {},
    el("dt", { text: "Made with" }), el("dd", { text: p.producer || "unknown" }),
    el("dt", { text: "Beds" }), el("dd", { text: p.beds.join(", ") }),
    el("dt", { text: "Settings" }), el("dd", {
      text: p.has_settings ? `${p.print_settings || "?"} on ${p.printer_settings || "?"}` : "none in file",
    }));
  $("info").replaceChildren(dl);
  $("info").hidden = false;
}

function renderBeds() {
  const p = state.project;
  const box = $("beds");
  box.replaceChildren(...p.beds.map((b) => el("label", {},
    el("input", { type: "checkbox", value: String(b), checked: true }), `Bed ${b}`)));
  $("beds-row").hidden = p.beds.length < 2;
}

function selectedBeds() {
  const boxes = [...$("beds").querySelectorAll("input")];
  const on = boxes.filter((b) => b.checked).map((b) => Number(b.value));
  return on.length === boxes.length ? [] : on;
}

async function onBedsChange() {
  const boxes = [...$("beds").querySelectorAll("input")];
  if (!boxes.some((b) => b.checked)) { updateButton(); return; }
  try {
    const keep = state.project;
    const fresh = await call("inspect", { beds: selectedBeds() });
    state.project = Object.assign({}, keep, { used: fresh.used, suggested_map: fresh.suggested_map });
    renderFilaments();
  } catch (e) { /* keep the previous list */ }
  updateButton();
}

function renderFilaments() {
  const p = state.project;
  if (!p) { $("fil-card").hidden = true; return; }
  $("fil-card").hidden = !p.used.length;
  const printer = $("printer").value;
  const isPreset = printer !== PRUSA && printer !== FILE;
  const table = currentTable();
  const usePresets = isPreset && table && settingsMode() !== "none";
  $("fil-preset-head").hidden = !usePresets;
  const keep = $("keep-numbers").checked;
  const old = new Map([...$("fil-rows").querySelectorAll("tr")].map((tr) => [tr.dataset.ext,
    { slot: tr.querySelector("input").value, fil: tr.querySelector("select") ? tr.querySelector("select").value : "" }]));
  const d = usePresets ? printerDefaults(table, printer) : null;
  const rows = p.used.map((u) => {
    const prev = old.get(String(u.extruder));
    const slot = el("input", {
      type: "number", min: "1", max: "64", "aria-label": `Slot for extruder ${u.extruder}`,
      value: keep ? String(u.extruder) : (prev ? prev.slot : String(p.suggested_map[String(u.extruder)] || u.extruder)),
    });
    slot.disabled = keep;
    slot.addEventListener("input", updateButton);
    const swatch = el("span", { class: "swatch" });
    if (/^#[0-9a-f]{6}/i.test(u.colour)) swatch.style.background = u.colour.slice(0, 7);
    const cells = [
      el("td", {}, swatch, `${u.extruder} `, el("span", { class: "hint", text: [u.type, u.colour].filter(Boolean).join(" ") })),
      el("td", {}, slot),
    ];
    if (usePresets) {
      const want = u.generic_type;
      const same = [], other = [];
      for (const n of d.fils) (normalizedType(first(table.filament[n].filament_type)) === want ? same : other).push(n);
      const sel = el("select", { "aria-label": `Filament preset for extruder ${u.extruder}` },
        el("option", { value: "", text: "Auto" }),
        same.length ? el("optgroup", { label: `${want} filaments` }, ...same.map((n) => el("option", { value: n, text: n }))) : null,
        other.length ? el("optgroup", { label: want ? "Other materials" : "Filaments" },
          ...other.map((n) => el("option", { value: n, text: n }))) : null);
      if (prev && prev.fil && d.fils.includes(prev.fil)) sel.value = prev.fil;
      cells.push(el("td", {}, sel));
    }
    const tr = el("tr", { "data-ext": String(u.extruder) }, ...cells);
    return tr;
  });
  $("fil-rows").replaceChildren(...rows);
}

function filamentMap() {
  const map = {};
  for (const tr of $("fil-rows").querySelectorAll("tr")) {
    const v = Number(tr.querySelector("input").value);
    if (!Number.isInteger(v) || v < 1) return null;
    map[tr.dataset.ext] = v;
  }
  return map;
}

function updateButton() {
  const p = $("printer").value;
  const beds = [...$("beds").querySelectorAll("input")];
  const ok = Boolean(pythonReady && state.project && !state.busy && filamentMap() !== null &&
    (!beds.length || beds.some((b) => b.checked)) && (p !== FILE || state.printerFile));
  $("convert").disabled = !ok;
}

// -- convert ----------------------------------------------------------------------------------
function outputName() {
  const base = state.fileName.replace(/\.3mf$/i, "");
  return `${base}_${slicer() === "orca" ? "orca" : "bambu"}.3mf`;
}

async function onConvert() {
  const s = slicer();
  const printer = $("printer").value;
  const fmap = filamentMap();
  const request = {
    slicer: s,
    output: outputName(),
    settings: settingsMode(),
    beds: selectedBeds(),
    split: $("split").checked,
    filament_map: fmap,
  };
  const tables = [];
  if (printer === PRUSA) {
    request.printer = "prusa";
  } else if (printer === FILE) {
    request.printer = "file";
    request.printer_file = state.printerFile.path;
    if (state.printerFile.vendor) tables.push(await loadTable(s, state.printerFile.vendor));
  } else {
    const t = await loadTable(s, vendorEntry());
    tables.push(t);
    request.printer = "preset";
    request.printer_name = printer;
    request.process = $("process").value;
    savePrefs({ process: Object.assign({}, loadPrefs().process, { [s + "|" + printer]: request.process }) });
    // One entry per filament slot: an explicit pick from the first extruder on that slot is kept
    // as it is; other slots take the printer default, which the converter swaps for a Generic
    // preset of the PrusaSlicer material when the materials differ.
    const d = printerDefaults(t.json, printer);
    const slots = Math.max(1, ...Object.values(fmap));
    const fils = Array.from({ length: slots }, () => ({ name: d.fil, locked: false }));
    const seen = new Set();
    for (const tr of $("fil-rows").querySelectorAll("tr")) {
      const slot = Number(tr.querySelector("input").value);
      const sel = tr.querySelector("select");
      if (seen.has(slot)) continue;
      seen.add(slot);
      if (sel && sel.value) fils[slot - 1] = { name: sel.value, locked: true };
    }
    request.filaments = fils;
  }
  state.busy = true;
  updateButton();
  const res = $("result");
  res.replaceChildren(el("div", { class: "msg" }, el("span", { class: "spinner" }), " Converting…"));
  try {
    const r = await call("convert", {
      request,
      tables: tables.map((t) => ({ slicer: t.slicer, file: t.file, text: t.text })),
    });
    renderResult(r);
  } catch (e) {
    res.replaceChildren(el("div", { class: "msg err" },
      el("strong", { text: "Conversion failed: " }), e.message,
      e.detail && e.detail !== e.message ? el("details", {}, el("summary", { text: "Details" }), el("pre", { text: e.detail })) : null));
  } finally {
    state.busy = false;
    updateButton();
  }
}

function renderResult(r) {
  const links = r.files.map((f) => {
    const url = URL.createObjectURL(new Blob([f.bytes], { type: "application/vnd.ms-package.3dmanufacturing-3dmodel+xml" }));
    return el("a", { href: url, download: f.name, text: `Download ${f.name}` });
  });
  const facts = [
    `${r.objects} object(s), ${r.parts} part(s), ${r.build_items} build item(s), ${r.painted_triangles} painted triangle(s)`,
  ];
  if (r.files.length === 1 && r.plates > 1) facts.push(`${r.plates} beds placed on plates 1–${r.plates}`);
  const remap = Object.entries(r.filament_map);
  if (remap.length) facts.push("Extruders → filament slots: " + remap.map(([k, v]) => `${k}→${v}`).join(", "));
  if (r.settings_written) {
    facts.push(`Printer: ${r.printer}`);
    if (r.process) facts.push(`Process: ${r.process}`);
    r.filaments.forEach((n, i) => facts.push(`Filament ${i + 1}: ${n}`));
    if (r.colour_changes) facts.push(`Colour changes / pauses carried over: ${r.colour_changes}`);
    if (r.layer_ranges) facts.push(`Layer height ranges carried over: ${r.layer_ranges}`);
    if (r.text_parts) facts.push(`Editable text parts: ${r.text_parts}`);
  } else {
    facts.push(`Settings not converted (${r.description})`);
  }
  const out = [el("div", { class: "msg ok" },
    el("strong", { text: `Done (${r.slicer}).` }),
    el("div", { class: "downloads" }, ...links),
    el("ul", {}, ...facts.map((f) => el("li", { text: f }))))];
  if (r.warnings.length) {
    out.push(el("div", { class: "msg warn" }, el("strong", { text: "Notes" }),
      el("ul", {}, ...r.warnings.map((w) => el("li", { text: w })))));
  }
  $("result").replaceChildren(...out);
  if (links.length === 1) links[0].click();
}

// -- wiring -----------------------------------------------------------------------------------
function wire() {
  $("printer-file-info").dataset.text = $("printer-file-info").textContent;
  $("file").addEventListener("change", () => onFile($("file").files[0]));
  const drop = $("drop");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    if (e.dataTransfer.files.length) onFile(e.dataTransfer.files[0]);
  });
  for (const r of document.querySelectorAll("input[name=slicer]")) r.addEventListener("change", () => onSlicerChange().catch(showError));
  for (const r of document.querySelectorAll("input[name=settings]")) r.addEventListener("change", () => {
    savePrefs({ settings: settingsMode() });
    renderFilaments();
  });
  $("vendor").addEventListener("change", () => onVendorChange().catch(showError));
  $("printer").addEventListener("change", onPrinterChange);
  $("process").addEventListener("change", updateButton);
  $("printer-file").addEventListener("change", onPrinterFile);
  $("beds").addEventListener("change", onBedsChange);
  $("keep-numbers").addEventListener("change", renderFilaments);
  $("convert").addEventListener("click", onConvert);
}

function showError(e) {
  $("result").replaceChildren(el("div", { class: "msg err" }, String(e && e.message ? e.message : e)));
}

async function start() {
  wire();
  try {
    state.build = await (await fetchJson("build-info.json")).json();
  } catch (e) {
    state.build = { slicers: { bambu: {}, orca: {} } };
  }
  const prefs = loadPrefs();
  for (const r of document.querySelectorAll("input[name=slicer]")) {
    const have = state.build.slicers && state.build.slicers[r.value];
    r.disabled = !have;
    r.parentElement.classList.toggle("disabled", !have);
    if (have && prefs.slicer === r.value) r.checked = true;
  }
  if (document.querySelector("input[name=slicer]:checked").disabled) {
    const firstOk = document.querySelector("input[name=slicer]:not(:disabled)");
    if (firstOk) firstOk.checked = true;
  }
  if (prefs.settings) {
    const r = document.querySelector(`input[name=settings][value="${prefs.settings}"]`);
    if (r) r.checked = true;
  }
  if (state.build.version) $("app-version").textContent = "v" + state.build.version;
  const presets = onSlicerChange().catch((e) => showError("Could not load the preset list: " + e.message));
  try {
    await call("init");
    pythonReady = true;
    $("boot").className = "boot done";
  } catch (e) {
    bootFailed("The converter could not start: " + e.message +
      ". It needs a current browser and a connection to cdn.jsdelivr.net (Pyodide).");
  }
  await presets;
  updateButton();
}

start();
