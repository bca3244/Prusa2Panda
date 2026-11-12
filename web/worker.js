// Runs the Prusa2Panda converter (Python) in Pyodide, off the page's main thread.
//
// Messages in:  {id, cmd: "init" | "inspect" | "preset_file" | "convert", ...}
// Messages out: {id, ok: true, result} or {id, ok: false, error, detail}
"use strict";

const PYODIDE_VERSION = "0.29.5";
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

let ready = null; // Promise of the prusa2panda.web module
let pyodide = null;
let inputPath = null; // the PrusaSlicer project the page picked last

function status(text) {
  self.postMessage({ status: text });
}

async function boot() {
  status("Loading Python…");
  importScripts(PYODIDE_URL + "pyodide.js");
  pyodide = await loadPyodide({ indexURL: PYODIDE_URL });
  status("Loading lxml…");
  await pyodide.loadPackage("lxml");
  status("Loading the converter…");
  const resp = await fetch("py/prusa2panda.zip", { cache: "no-cache" });
  if (!resp.ok) throw new Error(`py/prusa2panda.zip: HTTP ${resp.status}`);
  pyodide.unpackArchive(await resp.arrayBuffer(), "zip", { extractDir: "/p2p" });
  pyodide.runPython("import sys; sys.path.insert(0, '/p2p')");
  for (const d of ["/in", "/out", "/presets"]) pyodide.FS.mkdirTree(d);
  return pyodide.pyimport("prusa2panda.web");
}

function clearDir(dir) {
  const FS = pyodide.FS;
  for (const name of FS.readdir(dir)) {
    if (name === "." || name === "..") continue;
    FS.unlink(`${dir}/${name}`);
  }
}

// Keep file names safe for the virtual file system (the user's name is kept for the output).
function safeName(name) {
  return String(name || "file").replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").slice(-150) || "file";
}

async function handle(msg) {
  const api = await ready;
  const FS = pyodide.FS;
  switch (msg.cmd) {
    case "init":
      return { version: api.version() };
    case "inspect": {
      if (msg.bytes) {
        clearDir("/in");
        inputPath = null;
        const path = "/in/" + safeName(msg.name);
        FS.writeFile(path, new Uint8Array(msg.bytes));
        inputPath = path;
      }
      if (!inputPath) throw new Error("no project loaded");
      const beds = msg.beds && msg.beds.length ? pyodide.toPy(msg.beds) : null;
      try {
        return JSON.parse(api.inspect(inputPath, beds));
      } finally {
        if (beds) beds.destroy();
      }
    }
    case "preset_file": {
      const path = "/presets/upload_" + safeName(msg.name);
      FS.writeFile(path, new Uint8Array(msg.bytes));
      return { path, printer: api.preset_file_printer(path) };
    }
    case "convert": {
      const tables = [];
      for (const t of msg.tables || []) {
        const path = "/presets/" + safeName(t.slicer + "_" + t.file);
        FS.writeFile(path, t.text);
        tables.push(path);
      }
      clearDir("/out");
      if (!inputPath) throw new Error("no project loaded");
      const req = Object.assign({}, msg.request, {
        input: inputPath, tables, output: "/out/" + safeName(msg.request.output),
      });
      const res = JSON.parse(api.convert(JSON.stringify(req)));
      res.files = res.outputs.map((p) => ({ name: p.split("/").pop(), bytes: FS.readFile(p) }));
      clearDir("/out");
      return res;
    }
    default:
      throw new Error("unknown command " + msg.cmd);
  }
}

self.onmessage = async (ev) => {
  const msg = ev.data;
  if (!ready) {
    ready = boot();
    ready.catch(() => {});
  }
  try {
    const result = await handle(msg);
    const transfer = (result && result.files) ? result.files.map((f) => f.bytes.buffer) : [];
    self.postMessage({ id: msg.id, ok: true, result }, transfer);
  } catch (e) {
    let error = String(e && e.message ? e.message : e);
    const detail = error;
    // A Python traceback: show its last line, keep the rest for the details view.
    const lines = error.trim().split("\n");
    if (lines.length > 1) error = lines[lines.length - 1];
    self.postMessage({ id: msg.id, ok: false, error, detail });
  }
};
