// Isolated browser check of the real TimetableBrowser and PDF download, using public test fixtures.
// No authentication, service requests, generation or persistence. Artifacts stay under ignored .next/.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import path from "node:path";
import { inflateSync } from "node:zlib";
import { metadata, sessions } from "./examination-pdf-fixture.mjs";
const evaluationResult = JSON.parse(readFileSync(new URL("./fixtures/examination-evaluation.json", import.meta.url), "utf8"));

const require = createRequire(import.meta.url);
const { webpack } = require("next/dist/compiled/webpack/webpack");
const root = path.resolve(import.meta.dirname, "..");
const work = path.join(root, ".next", "examination-pdf-browser");
const downloads = path.join(work, `downloads-${Date.now()}`);
mkdirSync(downloads, { recursive: true });
writeFileSync(path.join(work, "loader.cjs"), `module.exports = function(source) { return require(${JSON.stringify(require.resolve("typescript"))}).transpileModule(source, {compilerOptions:{jsx:4,target:9,module:99}}).outputText; };`);
writeFileSync(path.join(work, "link.jsx"), 'export default function Link({children, ...props}) { return <a {...props}>{children}</a> }');
writeFileSync(path.join(work, "styles.js"), 'export const fieldClass="field", primaryButton="primary", secondaryButton="secondary";');
writeFileSync(path.join(work, "entry.tsx"), `
import { createRoot } from "react-dom/client";
import { TimetableBrowser } from "@/app/(examination)/exams/generate/_components/timetable-browser";
import { EvaluationDashboard } from "@/app/(examination)/exams/generate/_components/evaluation-dashboard";
createRoot(document.getElementById("root")!).render(<><TimetableBrowser sessions={${JSON.stringify(sessions)}} valid statusMessage="" exportMetadata={${JSON.stringify(metadata)}} /><EvaluationDashboard result={${JSON.stringify(evaluationResult)}} /></>);
`);
await new Promise((resolve, reject) => webpack({
  mode: "production", target: "web", entry: path.join(work, "entry.tsx"),
  optimization: { minimize: false, minimizer: [] },
  output: { path: path.join(work, "bundle"), filename: "main.js", publicPath: "/", chunkFilename: "[id].js" },
  resolve: { extensions: [".tsx", ".ts", ".jsx", ".js"], alias: { "@": path.join(root, "src"), "next/link$": path.join(work, "link.jsx") } },
  module: { rules: [{ test: /\.(tsx?|jsx)$/, exclude: /node_modules/, use: path.join(work, "loader.cjs") }] },
  plugins: [new webpack.NormalModuleReplacementPlugin(/\.\/period-calendar$/, path.join(work, "styles.js"))],
  performance: { hints: false },
}, (error, stats) => error || stats.hasErrors() ? reject(error ?? new Error(stats.toString({ all: false, errors: true }))) : resolve()));
console.log("Browser check: test bundle compiled.");

const requests = [];
const server = createServer((request, response) => {
  const name = request.url === "/" ? "" : request.url.slice(1);
  requests.push(request.url);
  if (!name) { response.setHeader("Content-Type", "text/html"); response.end('<!doctype html><html lang="en"><body><div id="root"></div><script src="/main.js"></script></body></html>'); }
  else if (/^[\w.-]+\.js$/.test(name) && existsSync(path.join(work, "bundle", name))) { response.setHeader("Content-Type", "application/javascript"); response.end(readFileSync(path.join(work, "bundle", name))); }
  else { response.writeHead(404); response.end(); }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
console.log("Browser check: local test server listening.");
const chrome = process.env.CHROME_PATH ?? [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
].find(existsSync);
assert.ok(chrome, "Set CHROME_PATH to a local Chromium browser");
let socket;
const browser = spawn(chrome, ["--headless=new", "--disable-gpu", "--no-first-run", "--disable-background-networking", "--disable-extensions",
  "--remote-debugging-port=0", `--user-data-dir=${path.join(work, `profile-${Date.now()}`)}`, "about:blank"], { windowsHide: true, stdio: ["ignore", "ignore", "pipe"] });
try {
  const endpoint = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error("Browser debug endpoint timed out")), 20000);
    browser.once("error", (error) => { clearTimeout(timer); reject(error); });
    browser.stderr.on("data", (chunk) => { output += chunk; const match = output.match(/DevTools listening on (ws:\/\/[^\s]+)/); if (match) { clearTimeout(timer); resolve(match[1]); } });
  });
  socket = new WebSocket(endpoint);
  console.log("Browser check: isolated Chromium launched.");
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Browser WebSocket connection timed out")), 15000);
    socket.onopen = () => { clearTimeout(timer); resolve(); };
    socket.onerror = () => { clearTimeout(timer); reject(new Error("Browser WebSocket connection failed")); };
  });
  const pending = new Map(); const exceptions = []; let sequence = 0;
  socket.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (message.method === "Runtime.exceptionThrown") exceptions.push(message.params.exceptionDetails.text);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject, timer } = pending.get(message.id); pending.delete(message.id); clearTimeout(timer);
      if (message.error) reject(new Error(message.error.message)); else resolve(message.result);
    }
  };
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Browser command timed out: ${method}`)); }, 15000);
    pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  await send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: downloads, eventsEnabled: true });
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Runtime.enable", {}, sessionId);
  await send("Page.enable", {}, sessionId);
  await send("Page.navigate", { url: `http://127.0.0.1:${server.address().port}/` }, sessionId);
  const evaluate = async (expression) => (await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }, sessionId)).result.value;
  const until = async (condition, message) => {
    const end = Date.now() + 20000;
    while (Date.now() < end) { if (await condition()) return; await new Promise((resolve) => setTimeout(resolve, 100)); }
    throw new Error(message);
  };
  await until(() => evaluate('document.body.textContent.includes("Download PDF")'), "Result component did not mount");
  console.log("Browser check: real result component mounted.");
  const click = () => evaluate('[...document.querySelectorAll("button")].find(b=>b.textContent==="Download PDF").click()');
  await click();
  await until(() => readdirSync(downloads).includes("examination-TEST-FINAL-UNSAVED-all-years.pdf"), "All-years PDF did not download");
  await evaluate('const year = document.querySelector("select"); year.value="2"; year.dispatchEvent(new Event("change", {bubbles:true}));');
  await until(() => evaluate('document.body.textContent.includes("10 of 70 sessions shown")'), "Year display filter did not apply");
  await click();
  await until(() => readdirSync(downloads).includes("examination-TEST-FINAL-UNSAVED-year-2.pdf"), "Year-filtered PDF did not download");
  const bytes = readFileSync(path.join(downloads, "examination-TEST-FINAL-UNSAVED-year-2.pdf"));
  assert.equal(bytes.subarray(0, 5).toString(), "%PDF-");
  const contents = [...bytes.toString("latin1").matchAll(/stream\r?\n([\s\S]*?)\r?\nendstream/g)]
    .map((match) => inflateSync(Buffer.from(match[1], "latin1")).toString("latin1")).join("\n");
  assert.ok(contents.includes("TEST060")); assert.equal(contents.includes("TEST000"), false);
  await evaluate('const group=document.querySelector(\'[aria-label="Evaluation cohort filters"]\'); const y=group.querySelectorAll("select")[0]; y.value="2"; y.dispatchEvent(new Event("change",{bubbles:true}));');
  await evaluate('const s=document.querySelector(\'[aria-label="Evaluation cohort filters"]\').querySelectorAll("select")[1]; s.value="DS"; s.dispatchEvent(new Event("change",{bubbles:true}));');
  await until(() => evaluate('document.querySelector(\'section[aria-label="Student Workload Evaluation"]\').textContent.includes("Year 2 / DS")'), "Evaluation cohort filter did not apply");
  await evaluate('[...document.querySelectorAll("button")].find(b=>b.textContent==="Download Evaluation Report PDF").click()');
  await until(() => readdirSync(downloads).includes("evaluation-TEST-UNSAVED.pdf"), "Evaluation report did not download");
  const report = readFileSync(path.join(downloads, "evaluation-TEST-UNSAVED.pdf"));
  assert.equal(report.subarray(0, 5).toString(), "%PDF-");
  const reportText = [...report.toString("latin1").matchAll(/stream\r?\n([\s\S]*?)\r?\nendstream/g)]
    .map((match) => inflateSync(Buffer.from(match[1], "latin1")).toString("latin1")).join("\n");
  assert.ok(reportText.includes("EVALUATION REPORT")); assert.ok(reportText.includes("DS"));
  assert.equal(reportText.includes("IT30000001"), false);
  assert.deepEqual(exceptions, []);
  assert.equal(requests.some((url) => url.startsWith("/api")), false);
  console.log("PASS: real components download all-years, Year 2 and cohort-filtered evaluation PDFs; contents verified; no service requests.");
  console.log(`Artifacts: ${path.relative(root, downloads)}`);
  await send("Browser.close");
} finally {
  socket?.close(); browser.kill(); server.closeAllConnections(); server.close();
}
