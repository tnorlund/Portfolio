// Glyph Studio local server: font-source file API + python bridge.
// Zero-dep node:http. All writes confined to fonts/ and .out/.
import { execFile } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { promisify } from "node:util";
import {
  BITMATRIX_DIR,
  FONTS_DIR,
  OUT_DIR,
  PORT,
  PYTHON,
  PY_ENV,
  SAMPLES,
  STUDIO_ROOT,
  WORKTREE,
} from "./env.mjs";
import { call as pyCall } from "./pyworker.mjs";

const execFileP = promisify(execFile);

const json = (res, code, obj) => {
  res.writeHead(code, { "content-type": "application/json" });
  res.end(JSON.stringify(obj));
};

const readBody = (req) =>
  new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (c) => (data += c));
    req.on("end", () => resolve(data));
    req.on("error", reject);
  });

function fontPaths(name) {
  if (!/^[a-z0-9_-]+$/.test(name)) throw new Error("bad font name");
  const dir = path.join(FONTS_DIR, name);
  return {
    dir,
    font: path.join(dir, "font.json"),
    glyphs: path.join(dir, "glyphs"),
    traced: path.join(dir, "_traced"),
  };
}

async function atomicWrite(file, content) {
  const tmp = file + ".tmp";
  await fsp.mkdir(path.dirname(file), { recursive: true });
  await fsp.writeFile(tmp, content);
  await fsp.rename(tmp, file);
}

async function getFont(name) {
  const p = fontPaths(name);
  const font = JSON.parse(await fsp.readFile(p.font, "utf-8"));
  const glyphs = {};
  for (const f of await fsp.readdir(p.glyphs)) {
    if (!f.endsWith(".json")) continue;
    const g = JSON.parse(await fsp.readFile(path.join(p.glyphs, f), "utf-8"));
    glyphs[g.codepoint] = g;
  }
  let traced = [];
  try {
    traced = (await fsp.readdir(p.traced))
      .filter((f) => f.endsWith(".json"))
      .map((f) => parseInt(f.slice(1, 5), 16));
  } catch {}
  return { font, glyphs, traced };
}

async function compileFont(name) {
  const npz = path.join(OUT_DIR, `${name}-studio.glyphs.npz`);
  const sheet = path.join(OUT_DIR, `${name}-studio.sheet.png`);
  const { stdout } = await execFileP(
    PYTHON,
    ["-m", "glyphstudio.compile", fontPaths(name).dir, npz, "--sheet", sheet],
    { cwd: path.join(STUDIO_ROOT, "py"), env: PY_ENV, timeout: 180000 },
  );
  const atlas = await pyCall("atlas_json", { npz }, { timeoutMs: 60000 });
  return { log: stdout, npz, sheet: `/files/${name}-studio.sheet.png`,
           glyphs: atlas.glyphs };
}

async function reviewFont(name, review) {
  const npz = path.join(OUT_DIR, `${name}-studio.glyphs.npz`);
  if (!fs.existsSync(npz)) throw new Error("compile first");
  // BITMATRIX_DIR overlay: symlink everything, drop our npz over the profile's
  const overlay = path.join(OUT_DIR, "bitmatrix-overlay");
  await fsp.rm(overlay, { recursive: true, force: true });
  await fsp.mkdir(overlay, { recursive: true });
  for (const f of await fsp.readdir(BITMATRIX_DIR)) {
    await fsp.symlink(path.join(BITMATRIX_DIR, f), path.join(overlay, f))
      .catch(() => {});
  }
  const target = path.join(overlay, `${name}.glyphs.npz`);
  await fsp.rm(target, { force: true });
  await fsp.copyFile(npz, target);

  const out = path.join(OUT_DIR, `${name}-review.png`);
  const { merchant, imageId, receiptId } = review;
  const { stdout } = await execFileP(
    PYTHON,
    [
      path.join(WORKTREE, "synthesis_loop", "glyph_review.py"),
      "receipt", merchant, imageId, String(receiptId), out,
    ],
    {
      cwd: WORKTREE,
      env: { ...PY_ENV, BITMATRIX_DIR: overlay },
      timeout: 300000,
    },
  );
  let scorecard = null;
  const scorePath = out.replace(/\.png$/, ".scorecard.json");
  try {
    scorecard = JSON.parse(await fsp.readFile(scorePath, "utf-8"));
  } catch {}
  return {
    log: stdout,
    png: `/files/${name}-review.png`,
    summary: scorecard?.summary ?? null,
    failures: scorecard?.failures ?? [],
  };
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const parts = url.pathname.split("/").filter(Boolean);
  try {
    // GET /files/* — static from .out
    if (req.method === "GET" && parts[0] === "files") {
      const file = path.normalize(path.join(OUT_DIR, ...parts.slice(1)));
      if (!file.startsWith(OUT_DIR)) return json(res, 403, { error: "nope" });
      const ext = path.extname(file);
      const type = ext === ".png" ? "image/png" : "application/octet-stream";
      res.writeHead(200, { "content-type": type });
      fs.createReadStream(file).on("error", () => res.end()).pipe(res);
      return;
    }

    if (parts[0] !== "api") return json(res, 404, { error: "not found" });

    // GET /api/font/:name
    if (req.method === "GET" && parts[1] === "font" && parts.length === 3) {
      return json(res, 200, await getFont(parts[2]));
    }
    // PUT /api/font/:name/font
    if (req.method === "PUT" && parts[1] === "font" && parts[3] === "font") {
      const body = await readBody(req);
      JSON.parse(body);
      await atomicWrite(fontPaths(parts[2]).font, body);
      return json(res, 200, { ok: true });
    }
    // PUT /api/font/:name/glyph/:cp
    if (req.method === "PUT" && parts[1] === "font" && parts[3] === "glyph") {
      const cp = parseInt(parts[4], 10);
      const body = JSON.parse(await readBody(req));
      if (body.codepoint !== cp) return json(res, 400, { error: "cp mismatch" });
      const p = fontPaths(parts[2]);
      const file = path.join(p.glyphs, `u${cp.toString(16).padStart(4, "0")}.json`);
      await atomicWrite(file, JSON.stringify(body, null, 1) + "\n");
      return json(res, 200, { ok: true });
    }
    // POST /api/font/:name/glyph/:cp/adopt-trace
    if (req.method === "POST" && parts[3] === "glyph" && parts[5] === "adopt-trace") {
      const cp = parseInt(parts[4], 10);
      const p = fontPaths(parts[2]);
      const fname = `u${cp.toString(16).padStart(4, "0")}.json`;
      await fsp.rename(path.join(p.traced, fname), path.join(p.glyphs, fname));
      return json(res, 200, { ok: true });
    }
    // GET /api/samples/:name/:cp.png?mode=&i=
    if (req.method === "GET" && parts[1] === "samples") {
      const name = parts[2];
      const cp = parseInt(parts[3], 10);
      const samples = SAMPLES[name];
      if (!samples) return json(res, 404, { error: "no corpus" });
      const result = await pyCall("sample_png", {
        samples, codepoint: cp,
        mode: url.searchParams.get("mode") || "median",
        i: Number(url.searchParams.get("i") || 0),
      });
      res.writeHead(200, {
        "content-type": "image/png",
        "x-ref-cap": String(result.refCap),
        "x-baseline-row": String(result.baselineRow),
        "x-n": String(result.n),
      });
      return res.end(Buffer.from(result.png_b64, "base64"));
    }
    // POST /api/raster  {glyph, params, refCap}
    if (req.method === "POST" && parts[1] === "raster") {
      const body = JSON.parse(await readBody(req));
      const result = await pyCall("raster_glyph", body);
      return json(res, 200, result);
    }
    // POST /api/compile/:name
    if (req.method === "POST" && parts[1] === "compile") {
      return json(res, 200, await compileFont(parts[2]));
    }
    // POST /api/review/:name
    if (req.method === "POST" && parts[1] === "review") {
      const { font } = await getFont(parts[2]);
      const review = font.review || {};
      if (!review.imageId) return json(res, 400, { error: "font.json review block missing" });
      return json(res, 200, await reviewFont(parts[2], review));
    }
    // POST /api/trace/:name  {chars?, force?}
    if (req.method === "POST" && parts[1] === "trace") {
      const body = JSON.parse((await readBody(req)) || "{}");
      const samples = SAMPLES[parts[2]];
      const args = ["-m", "glyphstudio.trace", samples, fontPaths(parts[2]).dir];
      if (body.chars) args.push("--chars", body.chars);
      if (body.force) args.push("--force");
      const { stdout } = await execFileP(PYTHON, args, {
        cwd: path.join(STUDIO_ROOT, "py"), env: PY_ENV, timeout: 300000,
      });
      return json(res, 200, { log: stdout });
    }
    json(res, 404, { error: "not found" });
  } catch (err) {
    json(res, 500, { error: String(err?.message || err) });
  }
});

server.listen(PORT, () => {
  console.log(`glyph-studio server on http://localhost:${PORT}`);
});
