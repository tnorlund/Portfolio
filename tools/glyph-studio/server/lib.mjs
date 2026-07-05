// Shared Glyph Studio core: font-source IO + python bridge orchestration.
// Both the HTTP server (server.mjs) and the stdio MCP server (mcp.mjs) import
// from here so there is exactly ONE implementation of the file/compile logic.
// All writes are confined to fonts/ and .out/.
import { execFile } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import {
  BITMATRIX_DIR,
  FONTS_DIR,
  OUT_DIR,
  PYTHON,
  PY_ENV,
  SAMPLES,
  STUDIO_ROOT,
  WORKTREE,
} from "./env.mjs";
import { call as pyCall } from "./pyworker.mjs";

const execFileP = promisify(execFile);

// Re-export env so consumers can pull everything from one module if they like.
export {
  BITMATRIX_DIR,
  FONTS_DIR,
  OUT_DIR,
  PYTHON,
  PY_ENV,
  SAMPLES,
  STUDIO_ROOT,
  WORKTREE,
  pyCall,
};

export function fontPaths(name) {
  if (!/^[a-z0-9_-]+$/.test(name)) throw new Error("bad font name");
  const dir = path.join(FONTS_DIR, name);
  return {
    dir,
    font: path.join(dir, "font.json"),
    glyphs: path.join(dir, "glyphs"),
    traced: path.join(dir, "_traced"),
  };
}

export function glyphFilename(cp) {
  return `u${cp.toString(16).padStart(4, "0")}.json`;
}

export function glyphFile(name, cp) {
  return path.join(fontPaths(name).glyphs, glyphFilename(cp));
}

export async function atomicWrite(file, content) {
  const tmp = file + ".tmp";
  await fsp.mkdir(path.dirname(file), { recursive: true });
  await fsp.writeFile(tmp, content);
  await fsp.rename(tmp, file);
}

export async function getFont(name) {
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

// Font params with per-glyph overrides applied (mirrors schema.py merged_params).
export function mergedParams(font, glyph) {
  const params = JSON.parse(JSON.stringify(font?.params || {}));
  const overrides = (glyph && glyph.overrides) || {};
  for (const [key, val] of Object.entries(overrides)) {
    if (
      val && typeof val === "object" && !Array.isArray(val) &&
      params[key] && typeof params[key] === "object"
    ) {
      Object.assign(params[key], val);
    } else {
      params[key] = val;
    }
  }
  return params;
}

// compile/review write shared paths in .out/; serialize them across both the
// HTTP and MCP entry points (and against parallel agent tool calls) with a
// simple in-process promise-chain mutex.
let _chain = Promise.resolve();
export function withCompileLock(fn) {
  const run = _chain.then(fn, fn);
  _chain = run.then(
    () => {},
    () => {},
  );
  return run;
}

export function compileFont(name) {
  return withCompileLock(async () => {
    const npz = path.join(OUT_DIR, `${name}-studio.glyphs.npz`);
    const sheet = path.join(OUT_DIR, `${name}-studio.sheet.png`);
    const { stdout } = await execFileP(
      PYTHON,
      ["-m", "glyphstudio.compile", fontPaths(name).dir, npz, "--sheet", sheet],
      { cwd: path.join(STUDIO_ROOT, "py"), env: PY_ENV, timeout: 180000 },
    );
    const atlas = await pyCall("atlas_json", { npz }, { timeoutMs: 60000 });
    return {
      log: stdout,
      npz,
      sheetPath: sheet,
      sheet: `/files/${name}-studio.sheet.png`,
      glyphs: atlas.glyphs,
    };
  });
}

export function reviewFont(name, review) {
  return withCompileLock(async () => {
    const npz = path.join(OUT_DIR, `${name}-studio.glyphs.npz`);
    if (!fs.existsSync(npz)) throw new Error("compile first");
    // BITMATRIX_DIR overlay: symlink everything, drop our npz over the profile's
    const overlay = path.join(OUT_DIR, "bitmatrix-overlay");
    await fsp.rm(overlay, { recursive: true, force: true });
    await fsp.mkdir(overlay, { recursive: true });
    for (const f of await fsp.readdir(BITMATRIX_DIR)) {
      await fsp
        .symlink(path.join(BITMATRIX_DIR, f), path.join(overlay, f))
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
        "receipt",
        merchant,
        imageId,
        String(receiptId),
        out,
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
      pngPath: out,
      png: `/files/${name}-review.png`,
      summary: scorecard?.summary ?? null,
      failures: scorecard?.failures ?? [],
    };
  });
}

// Downscale a PNG file to <= maxDim tall and return bare base64 (for MCP image
// blocks). Reuses the venv PIL via a one-shot exec — never on the raster hot
// path (only for the already-slow compile/review sheets).
export async function pngFileToB64(srcPath, maxDim = 1000) {
  const script = [
    "import sys, io, base64",
    "from PIL import Image",
    "src, maxdim = sys.argv[1], int(sys.argv[2])",
    "im = Image.open(src)",
    "w, h = im.size",
    "if h > maxdim:",
    "    s = maxdim / h",
    "    im = im.resize((max(1, round(w * s)), maxdim), Image.LANCZOS)",
    "buf = io.BytesIO(); im.save(buf, format='PNG')",
    "sys.stdout.write(base64.b64encode(buf.getvalue()).decode())",
  ].join("\n");
  const { stdout } = await execFileP(
    PYTHON,
    ["-c", script, srcPath, String(maxDim)],
    { env: PY_ENV, timeout: 30000, maxBuffer: 64 * 1024 * 1024 },
  );
  return stdout.trim();
}
