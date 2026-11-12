// Headless-browser smoke test of the web version: real conversions in Chromium.
//
//   node tools/smoke_web.js <site dir> <PrusaSlicer .3mf> <download dir> [--cdn-dir DIR]
//
// Serves the built site (tools/build_web.py) on localhost, opens it in Chromium, converts the
// project in several modes and saves each download. tools/check_web_outputs.py then checks
// the downloaded files. --cdn-dir answers requests for the Pyodide CDN from a local folder
// (for machines without access to cdn.jsdelivr.net).
"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const { chromium } = require("playwright");

const TYPES = {
  ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".zip": "application/zip", ".wasm": "application/wasm",
  ".whl": "application/zip", ".tar": "application/x-tar",
};

function serve(root) {
  const server = http.createServer((req, res) => {
    const rel = decodeURIComponent(new URL(req.url, "http://x").pathname).replace(/^\/+/, "") || "index.html";
    const file = path.join(root, rel);
    if (!file.startsWith(path.resolve(root)) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404);
      res.end("not found");
      return;
    }
    res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

// Each case: the page settings, then what check_web_outputs.py expects of the file.
const CASES = [
  // The smoke project's only extruder is PETG: Auto must swap the PLA default for Generic PETG.
  { name: "bambu_presets", slicer: "bambu", printer: "Bambu Lab P1S 0.4 nozzle", settings: "presets",
    expect_filaments: ["Generic PETG"] },
  { name: "bambu_prusa-settings", slicer: "bambu", printer: "Bambu Lab X1 Carbon 0.4 nozzle", settings: "prusa" },
  { name: "orca_presets", slicer: "orca", vendor: "BBL", printer: "Bambu Lab P2S 0.4 nozzle", settings: "presets" },
  // An explicit filament pick is kept even though the PrusaSlicer filament is PETG.
  { name: "bambu_locked-filament", slicer: "bambu", printer: "Bambu Lab P1S 0.4 nozzle", settings: "presets",
    filament: "Generic PLA" },
  { name: "orca_prusa-printer", slicer: "orca", printer: "__prusa__", settings: "prusa" },
];

async function main() {
  const args = process.argv.slice(2);
  let cdnDir = null;
  const i = args.indexOf("--cdn-dir");
  if (i >= 0) { cdnDir = args[i + 1]; args.splice(i, 2); }
  const [site, input, outDir] = args;
  if (!site || !input || !outDir) {
    console.error("usage: node tools/smoke_web.js <site dir> <input.3mf> <download dir> [--cdn-dir DIR]");
    process.exit(2);
  }
  fs.mkdirSync(outDir, { recursive: true });
  const server = await serve(path.resolve(site));
  const url = `http://127.0.0.1:${server.address().port}/`;
  const browser = await chromium.launch();
  const context = await browser.newContext({ acceptDownloads: true });
  if (cdnDir) {
    await context.route(/^https:\/\/cdn\.jsdelivr\.net\/pyodide\/[^/]+\/full\/(.*)$/, (route) => {
      const name = route.request().url().replace(/^.*\/full\//, "").split("?")[0];
      const file = path.join(cdnDir, name);
      if (!fs.existsSync(file)) return route.fulfill({ status: 404, body: "not found" });
      return route.fulfill({ status: 200, body: fs.readFileSync(file),
        headers: { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream",
                   "Access-Control-Allow-Origin": "*" } });
    });
  }
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(url);
  await page.waitForSelector("#boot.done", { state: "attached", timeout: 180000 });
  console.log("converter started");

  await page.setInputFiles("#file", input);
  await page.waitForFunction(() => !document.querySelector("#info").hidden ||
    /Could not/.test(document.querySelector("#drop-text").textContent), null, { timeout: 180000 });
  const dropText = await page.textContent("#drop-text");
  if (/Could not/.test(dropText)) throw new Error(dropText);
  console.log("project read: " + (await page.textContent("#info")));
  const report = [];
  for (const c of CASES) {
    await page.check(`input[name=slicer][value=${c.slicer}]`);
    await page.waitForFunction((v) => document.querySelector("#printer option") &&
      (!v || document.querySelector("#vendor").value === v || [...document.querySelectorAll("#vendor option")].some((o) => o.value === v)), c.vendor || "");
    if (c.vendor) await page.selectOption("#vendor", c.vendor);
    await page.waitForFunction((p) => [...document.querySelectorAll("#printer option")].some((o) => o.value === p), c.printer);
    await page.selectOption("#printer", c.printer);
    await page.check(`input[name=settings][value=${c.settings}]`);
    if (c.printer !== "__prusa__") await page.selectOption("#fil-rows tr select", c.filament || "");
    await page.waitForFunction(() => !document.querySelector("#convert").disabled, null, { timeout: 60000 });
    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: 120000 }),
      page.click("#convert"),
    ]);
    const file = path.join(outDir, `${c.name}.3mf`);
    await download.saveAs(file);
    const text = await page.textContent("#result");
    console.log(`${c.name}: saved ${download.suggestedFilename()}`);
    report.push(Object.assign({}, c, { file, result: text }));
  }
  fs.writeFileSync(path.join(outDir, "cases.json"), JSON.stringify(report, null, 1));
  await page.screenshot({ path: path.join(outDir, "page.png"), fullPage: true });
  await browser.close();
  server.close();
  const real = errors.filter((e) => !/favicon/.test(e));
  if (real.length) {
    console.error("browser errors:\n" + real.join("\n"));
    process.exit(1);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
