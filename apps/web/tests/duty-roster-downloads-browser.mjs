// Isolated browser check of the real download controls using already-exported Neon files.
// Does not call production APIs, alter auth, create fixtures or write database records.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";

const fixture = process.env.DUTY_ROSTER_EXPORT_JSON;
assert.ok(fixture, "Set DUTY_ROSTER_EXPORT_JSON to a read-only export of an existing saved roster.");
const data = JSON.parse(readFileSync(fixture, "utf8"));
const require = createRequire(import.meta.url);
const { webpack } = require("next/dist/compiled/webpack/webpack");
const root = path.resolve(import.meta.dirname, "..");
const work = mkdtempSync(path.join(tmpdir(), "schedence-roster-browser-"));
const downloads = path.join(work, "downloads"); mkdirSync(downloads);
writeFileSync(path.join(work, "loader.cjs"), `module.exports=s=>require(${JSON.stringify(require.resolve("typescript"))}).transpileModule(s,{compilerOptions:{jsx:4,target:9,module:99}}).outputText;`);
writeFileSync(path.join(work, "entry.tsx"), `import {createRoot} from ${JSON.stringify(require.resolve("react-dom/client"))};
import {RosterDownloads} from ${JSON.stringify(path.join(root, "src/app/(examination)/exams/duties/rosters/[rosterId]/roster-downloads.tsx"))};
createRoot(document.getElementById("root")!).render(<RosterDownloads rosterId=${JSON.stringify(data.roster.roster_id)} rowVersion={${data.roster.row_version}}/>);`);
await new Promise((resolve, reject) => webpack({
  mode: "production", target: "web", entry: path.join(work, "entry.tsx"),
  optimization: { minimize: false, minimizer: [] },
  output: { path: path.join(work, "bundle"), filename: "main.js" },
  resolve: { extensions: [".tsx", ".ts", ".js"], modules: [path.join(root, "node_modules"), "node_modules"], alias: { "@": path.join(root, "src") } },
  module: { rules: [{ test: /\.tsx?$/, exclude: /node_modules/, use: path.join(work, "loader.cjs") }] },
  performance: { hints: false },
}, (error, stats) => error || stats.hasErrors() ? reject(error ?? new Error(stats.toString({ all: false, errors: true }))) : resolve()));
console.log("Roster browser check: bundle built.");

let failNext = false;
const requests = [];
const server = createServer((request, response) => {
  const url = new URL(request.url, "http://localhost");
  if (url.pathname === "/") { response.setHeader("Content-Type", "text/html"); response.end('<div id="root"></div><script src="/main.js"></script>'); }
  else if (url.pathname === "/main.js") { response.setHeader("Content-Type", "application/javascript"); response.end(readFileSync(path.join(work, "bundle/main.js"))); }
  else if (url.pathname === "/test/fail-next") { failNext = true; response.end("ok"); }
  else if (url.pathname.includes(`/rosters/${data.roster.roster_id}/download/`)) {
    requests.push(request.url);
    if (failNext || url.searchParams.get("version") !== String(data.roster.row_version)) {
      failNext = false; response.writeHead(409, { "Content-Type": "application/json" }); response.end(JSON.stringify({ message: "The roster changed. Refresh the page." }));
    } else {
      const format = url.pathname.split("/").at(-1);
      if (!["xlsx", "pdf"].includes(format)) { response.writeHead(404); response.end(); return; }
      response.writeHead(200, { "Content-Type": format === "pdf" ? "application/pdf" : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
      response.end(readFileSync(path.join(path.dirname(fixture), `roster.${format}`)));
    }
  } else { response.writeHead(404); response.end(); }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const chrome = process.env.CHROME_PATH ?? ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].find(existsSync);
assert.ok(chrome, "Chromium browser is required for this opt-in check.");
const browser = spawn(chrome, ["--headless=new", "--disable-gpu", "--no-first-run", "--disable-background-networking", "--disable-extensions", "--remote-debugging-port=0", `--user-data-dir=${path.join(work, "profile")}`, "about:blank"], { windowsHide: true, stdio: ["ignore", "ignore", "pipe"] });
let socket;
try {
  const endpoint = await new Promise((resolve, reject) => {
    let output = ""; const timer = setTimeout(() => reject(new Error("Browser start timed out")), 20_000);
    browser.once("error", reject);
    browser.stderr.on("data", (chunk) => { output += chunk; const match = output.match(/DevTools listening on (ws:\/\/\S+)/); if (match) { clearTimeout(timer); resolve(match[1]); } });
  });
  socket = new WebSocket(endpoint);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let sequence = 0; const pending = new Map();
  socket.onmessage = ({ data }) => {
    const reply = JSON.parse(data), request = pending.get(reply.id);
    if (request) {
      clearTimeout(request.timer); pending.delete(reply.id);
      if (reply.error) request.reject(new Error(reply.error.message));
      else request.resolve(reply.result);
    }
  };
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence, timer = setTimeout(() => reject(new Error(`Browser command timed out: ${method}`)), 20_000);
    pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  await send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: downloads });
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  console.log("Roster browser check: isolated browser attached.");
  const evaluate = async (expression) => (await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }, sessionId)).result.value;
  const until = async (condition) => {
    const end = Date.now() + 20_000;
    while (Date.now() < end) { if (await condition()) return; await new Promise((resolve) => setTimeout(resolve, 100)); }
    throw new Error("Browser check timed out");
  };
  await send("Page.navigate", { url: `http://127.0.0.1:${server.address().port}/` }, sessionId);
  await until(() => evaluate('document.body.textContent.includes("Download Excel")'));
  for (const [format, text] of [["xlsx", "Excel"], ["pdf", "PDF"]]) {
    await evaluate(`[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Download ${text}')).click()`);
    await until(() => readdirSync(downloads).some((name) => name.endsWith(`.${format}`)));
    const filename = readdirSync(downloads).find((name) => name.endsWith(`.${format}`));
    assert.deepEqual(readFileSync(path.join(downloads, filename)), readFileSync(path.join(path.dirname(fixture), `roster.${format}`)));
    await until(() => evaluate('!document.querySelector("button").disabled'));
  }
  await send("Page.reload", {}, sessionId);
  await until(() => evaluate('document.body.textContent.includes("Download PDF")'));
  await evaluate('fetch("/test/fail-next")');
  await evaluate('[...document.querySelectorAll("button")].find(b=>b.textContent.includes("Download PDF")).click()');
  await until(() => evaluate('document.querySelector("[role=alert]")?.textContent.includes("Refresh")'));
  assert.equal(requests.length, 3);
  assert.ok(requests.every((url) => url.endsWith(`version=${data.roster.row_version}`)));
  console.log(JSON.stringify({ excel_download: "PASS", pdf_download: "PASS", downloaded_bytes_match_saved_exports: true, refresh: "PASS", failure_alert: "PASS", saved_assignments: data.assignments.length, artifacts: work }));
  await send("Browser.close");
} finally { socket?.close(); browser.kill(); server.close(); }
