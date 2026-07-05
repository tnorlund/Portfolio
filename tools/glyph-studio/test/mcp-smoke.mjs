// Scripted JSON-RPC smoke test for the glyph-studio MCP stdio server.
// Spawns mcp.mjs, drives initialize + tools/list + a set of tools/call, and
// asserts the round-trips. Exit 0 = pass. Run: node test/mcp-smoke.mjs
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(HERE, "..", "server", "mcp.mjs");
const FONT = "sprouts";

const proc = spawn("node", [SERVER], { stdio: ["pipe", "pipe", "inherit"] });

let buf = "";
const waiters = new Map();
proc.stdout.on("data", (chunk) => {
  buf += chunk.toString();
  let idx;
  while ((idx = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, idx);
    buf = buf.slice(idx + 1);
    if (!line.trim()) continue;
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      continue; // stray line — should never happen if stdout is clean
    }
    if (msg.id != null && waiters.has(msg.id)) {
      const w = waiters.get(msg.id);
      waiters.delete(msg.id);
      w(msg);
    }
  }
});

let nextId = 1;
function rpc(method, params) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`timeout waiting for ${method}`)),
      120000,
    );
    waiters.set(id, (msg) => {
      clearTimeout(timer);
      resolve(msg);
    });
    proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
  });
}
function notify(method, params) {
  proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n");
}

const call = (name, args) => rpc("tools/call", { name, arguments: args });
const blocks = (r) => r.result?.content ?? [];
const hasType = (r, t) => blocks(r).some((b) => b.type === t);

let failures = 0;
function check(label, cond, extra = "") {
  if (cond) {
    console.error(`  ok   ${label}`);
  } else {
    failures++;
    console.error(`  FAIL ${label} ${extra}`);
  }
}

async function run() {
  // 1. initialize handshake
  const init = await rpc("initialize", {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "smoke", version: "0" },
  });
  check("initialize returns serverInfo", init.result?.serverInfo?.name === "glyph-studio", JSON.stringify(init.result?.serverInfo));
  notify("notifications/initialized", {});

  // 2. tools/list
  const list = await rpc("tools/list", {});
  const toolNames = (list.result?.tools ?? []).map((t) => t.name).sort();
  const expected = [
    "compile_font", "font_audit", "get_glyph", "list_glyphs", "render_glyph",
    "review_font", "set_glyph", "simplify_glyphs", "view_samples",
  ];
  check(`tools/list has all 9 (${toolNames.length})`, expected.every((n) => toolNames.includes(n)), toolNames.join(","));

  // 3. get_glyph "A" -> text + image
  const g = await call("get_glyph", { font: FONT, char: "A" });
  check("get_glyph A has text block", hasType(g, "text"));
  check("get_glyph A has image block", hasType(g, "image"));

  // 4. list_glyphs -> structuredContent with 87+ glyphs
  const l = await call("list_glyphs", { font: FONT });
  check("list_glyphs >= 87 glyphs", (l.result?.structuredContent?.count ?? 0) >= 87, `count=${l.result?.structuredContent?.count}`);

  // 5. render_glyph by char -> image
  const rg = await call("render_glyph", { font: FONT, char: "A" });
  check("render_glyph A has image", hasType(rg, "image"));

  // 6. font_audit -> structuredContent rows with anatomy
  const fa = await call("font_audit", { font: FONT });
  const auditRows = fa.result?.structuredContent?.glyphs ?? [];
  check("font_audit returns rows", auditRows.length >= 87, `rows=${auditRows.length}`);
  check("font_audit rows have segment counts", auditRows[0] && "cubicSegs" in auditRows[0] && "lineSegs" in auditRows[0]);

  // 7. view_samples median -> image + n/refCap headers
  const vs = await call("view_samples", { font: FONT, char: "A", mode: "median" });
  check("view_samples A has image", hasType(vs, "image"));

  // 8. set_glyph dryRun on a valid loaded glyph (round-trip A) -> validates, no write
  const glyphA = JSON.parse(blocks(g).find((b) => b.type === "text")?.text ?? "{}");
  const dry = await call("set_glyph", { font: FONT, char: "A", glyph: glyphA, dryRun: true });
  check("set_glyph dryRun validates (not error)", !dry.result?.isError, JSON.stringify(blocks(dry)[0]));
  check("set_glyph dryRun did not write", dry.result?.structuredContent?.written === false);

  // 9. provenance guard: "i" is provenance=edited -> set without force must refuse
  const gi = await call("get_glyph", { font: FONT, char: "i" });
  const glyphI = JSON.parse(blocks(gi).find((b) => b.type === "text")?.text ?? "{}");
  const guarded = await call("set_glyph", { font: FONT, char: "i", glyph: glyphI, dryRun: false, force: false });
  const guardMsg = blocks(guarded).find((b) => b.type === "text")?.text ?? "";
  check("set_glyph refuses edited glyph without force", guarded.result?.isError === true && /edited/.test(guardMsg), guardMsg);

  // 10. set_glyph validation rejects a malformed glyph
  const bad = await call("set_glyph", { font: FONT, char: "A", glyph: { version: 1, char: "A", codepoint: 65, provenance: "traced", strokes: [] }, dryRun: true });
  check("set_glyph rejects empty strokes", bad.result?.isError === true, blocks(bad)[0]?.text);

  proc.stdin.end();
  proc.kill();

  console.error(`\n${failures === 0 ? "PASS" : "FAIL"}: ${failures} failure(s)`);
  process.exit(failures === 0 ? 0 : 1);
}

run().catch((e) => {
  console.error("smoke test error:", e);
  proc.kill();
  process.exit(1);
});
