// Glyph Studio MCP server (stdio). Sibling entry point to server.mjs over the
// SAME core (lib.mjs). Gives an agent the fast trace/render/compile/review loop
// as tools.
//
// PROTOCOL RULE: stdout is JSON-RPC framing. NEVER write to stdout (no
// console.log). Diagnostics go to stderr (console.error) only. The python
// worker is spawned with its stdout piped (pyworker.mjs), so it never leaks
// onto our stdout channel.
import { execFile } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

import {
  SAMPLES,
  FONT_MERCHANTS,
  renderCacheSlug,
  PYTHON,
  PY_ENV,
  STUDIO_ROOT,
  OUT_DIR,
  BITMATRIX_DIR,
  RENDER_CACHE_DIR,
  fontPaths,
  glyphFile,
  getFont,
  atomicWrite,
  mergedParams,
  compileFont,
  reviewFont,
  pngFileToB64,
  pyCall,
} from "./lib.mjs";

const execFileP = promisify(execFile);

// ---- small result helpers -------------------------------------------------
const errResult = (msg) => ({
  content: [{ type: "text", text: `Error: ${msg}` }],
  isError: true,
});
const imgBlock = (b64) => ({ type: "image", data: b64, mimeType: "image/png" });

// YYYYMMDD-HHMMSS (local) — matches the existing $BITMATRIX_DIR backup naming.
const tsStamp = (d = new Date()) => {
  const p = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}` +
    `-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
  );
};

// ---- glyph structural schema (mirrors py/glyphstudio/schema.py) -----------
const Pt = z.object({ x: z.number(), y: z.number() }).passthrough();
const NodeSchema = z
  .object({
    x: z.number(),
    y: z.number(),
    type: z.string().optional(),
    hIn: Pt.optional(),
    hOut: Pt.optional(),
  })
  .passthrough();
const StrokeSchema = z
  .object({
    closed: z.boolean().optional(),
    nodes: z.array(NodeSchema).min(2, "stroke needs >= 2 nodes"),
  })
  .passthrough();
const GlyphSchema = z
  .object({
    version: z.literal(1),
    char: z.string().min(1),
    codepoint: z.number().int(),
    provenance: z.enum(["traced", "edited"]),
    strokes: z.array(StrokeSchema).min(1, "glyph needs >= 1 stroke"),
  })
  .passthrough();

// ---- shared loaders -------------------------------------------------------
async function loadGlyph(fontName, char) {
  const cp = char.codePointAt(0);
  const file = glyphFile(fontName, cp);
  return JSON.parse(await fsp.readFile(file, "utf-8"));
}

async function rasterOf(fontSrc, glyph, refCap) {
  const params = mergedParams(fontSrc, glyph);
  return pyCall("raster_glyph", { glyph, params, refCap });
}

function countNodes(glyph) {
  return (glyph.strokes || []).reduce((n, s) => n + (s.nodes || []).length, 0);
}

// Per-glyph anatomy — same math as the font audit (segment is cubic iff the
// leaving node has hOut or the arriving node has hIn; a closed stroke adds the
// last->first pair).
function auditGlyph(g, fontSrc) {
  const capH = fontSrc?.metrics?.capHeight ?? 1000;
  let nodes = 0;
  let lineSegs = 0;
  let cubicSegs = 0;
  let closedLoops = 0;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const s of g.strokes || []) {
    const ns = s.nodes || [];
    nodes += ns.length;
    for (const n of ns) {
      if (typeof n.y === "number") {
        if (n.y < minY) minY = n.y;
        if (n.y > maxY) maxY = n.y;
      }
    }
    const pairs = [];
    for (let i = 0; i < ns.length - 1; i++) pairs.push([ns[i], ns[i + 1]]);
    if (s.closed && ns.length > 1) {
      pairs.push([ns[ns.length - 1], ns[0]]);
      closedLoops++;
    }
    for (const [a, b] of pairs) {
      if (a.hOut || b.hIn) cubicSegs++;
      else lineSegs++;
    }
  }
  return {
    char: g.char,
    codepoint: g.codepoint,
    provenance: g.provenance,
    strokes: (g.strokes || []).length,
    nodes,
    lineSegs,
    cubicSegs,
    closedLoops,
    width: g.width ?? null,
    descender: Number.isFinite(minY) ? minY < 0 : false,
    ascender: Number.isFinite(maxY) ? maxY > capH : false,
    minY: Number.isFinite(minY) ? minY : null,
    maxY: Number.isFinite(maxY) ? maxY : null,
    weight: g.overrides?.weight ?? fontSrc?.params?.weight ?? null,
  };
}

// ---------------------------------------------------------------------------
const server = new McpServer({ name: "glyph-studio", version: "1.0.0" });

server.registerTool(
  "list_glyphs",
  {
    title: "List glyph coverage",
    description:
      "Coverage table for a font: every glyph's char, codepoint, provenance, width, stroke/node counts, weight override, and whether a pending re-trace sits in _traced/.",
    inputSchema: { font: z.string() },
  },
  async ({ font }) => {
    try {
      const { font: fontSrc, glyphs, traced } = await getFont(font);
      const rows = Object.values(glyphs)
        .sort((a, b) => a.codepoint - b.codepoint)
        .map((g) => ({
          char: g.char,
          codepoint: g.codepoint,
          provenance: g.provenance,
          width: g.width ?? null,
          strokes: (g.strokes || []).length,
          nodes: countNodes(g),
          weightOverride: g.overrides?.weight ?? null,
          pendingTraced: traced.includes(g.codepoint),
        }));
      const text = rows
        .map(
          (r) =>
            `${r.char}  U+${r.codepoint
              .toString(16)
              .padStart(4, "0")
              .toUpperCase()}  ${r.provenance}${r.pendingTraced ? "*" : ""}  w=${r.width}  strokes=${r.strokes}  nodes=${r.nodes}${
              r.weightOverride != null ? `  wt=${r.weightOverride}` : ""
            }`,
        )
        .join("\n");
      return {
        content: [
          {
            type: "text",
            text: `${rows.length} glyphs in font "${font}" (* = pending re-trace in _traced/)\n${text}`,
          },
        ],
        structuredContent: { font, count: rows.length, glyphs: rows },
      };
    } catch (e) {
      return errResult(e.message);
    }
  },
);

server.registerTool(
  "get_glyph",
  {
    title: "Get glyph source + render",
    description:
      "Return the saved uNNNN.json source (pretty-printed) AND a rendered PNG of that glyph the way the receipt compositor draws it, so you can see source and pixels together.",
    inputSchema: { font: z.string(), char: z.string().min(1) },
  },
  async ({ font, char }) => {
    try {
      const { font: fontSrc } = await getFont(font);
      const glyph = await loadGlyph(font, char);
      const r = await rasterOf(fontSrc, glyph, 60);
      return {
        content: [
          { type: "text", text: JSON.stringify(glyph, null, 1) },
          imgBlock(r.png_b64),
          {
            type: "text",
            text: `render: w=${r.w} h=${r.h} off=${r.off} refCap=60`,
          },
        ],
      };
    } catch (e) {
      return errResult(e.message);
    }
  },
);

server.registerTool(
  "render_glyph",
  {
    title: "Render a candidate glyph",
    description:
      "Rasterize a glyph to a PNG the way the receipt compositor will draw it. Pass an UNSAVED candidate `glyph` JSON to preview edits without writing, or just `char` to render the saved glyph. The fast inner loop.",
    inputSchema: {
      font: z.string(),
      char: z.string().min(1).optional(),
      glyph: z.record(z.any()).optional(),
      refCap: z.number().int().default(60),
    },
  },
  async ({ font, char, glyph, refCap }) => {
    try {
      const { font: fontSrc } = await getFont(font);
      let g = glyph;
      if (!g) {
        if (!char) return errResult("provide either `glyph` or `char`");
        g = await loadGlyph(font, char);
      }
      const r = await rasterOf(fontSrc, g, refCap);
      return {
        content: [
          imgBlock(r.png_b64),
          {
            type: "text",
            text: `w=${r.w} h=${r.h} off=${r.off} refCap=${refCap}${
              glyph ? " (unsaved candidate)" : ""
            }`,
          },
        ],
      };
    } catch (e) {
      return errResult(e.message);
    }
  },
);

server.registerTool(
  "view_samples",
  {
    title: "View traced sample consensus",
    description:
      "Render the real-letterform corpus for a char: median (soft consensus), binary (thresholded consensus), index (a single sample i), or grid (the first up-to-9 individual samples montaged 3x3). Compare a candidate against the ground-truth ink the tracer saw.",
    inputSchema: {
      font: z.string(),
      char: z.string().min(1),
      mode: z.enum(["median", "binary", "index", "grid"]).default("median"),
      i: z.number().int().optional(),
    },
  },
  async ({ font, char, mode, i }) => {
    try {
      const samples = SAMPLES[font];
      if (!samples) return errResult(`no sample corpus configured for "${font}"`);
      const codepoint = char.codePointAt(0);
      const r = await pyCall("sample_png", {
        samples,
        codepoint,
        mode,
        i: i ?? 0,
      });
      return {
        content: [
          imgBlock(r.png_b64),
          {
            type: "text",
            text: `char="${char}" mode=${mode} n=${r.n} refCap=${r.refCap} baselineRow=${r.baselineRow} w=${r.w} h=${r.h}`,
          },
        ],
      };
    } catch (e) {
      return errResult(e.message);
    }
  },
);

server.registerTool(
  "set_glyph",
  {
    title: "Write a glyph source",
    description:
      "Validate then atomically write a glyph JSON to fonts/<font>/glyphs/. Structurally validates (schema v1), checks char/codepoint agreement, and test-rasterizes before writing. Refuses to overwrite a hand-'edited' glyph unless force=true. dryRun=true returns the verdict + a node/stroke diff without touching disk.",
    inputSchema: {
      font: z.string(),
      char: z.string().min(1),
      glyph: z.record(z.any()),
      dryRun: z.boolean().default(false),
      force: z.boolean().default(false),
    },
  },
  async ({ font, char, glyph, dryRun, force }) => {
    try {
      // (a) structural validation
      const parsed = GlyphSchema.safeParse(glyph);
      if (!parsed.success) {
        const issues = parsed.error.issues
          .map((i) => `${i.path.join(".") || "(root)"}: ${i.message}`)
          .join("; ");
        return errResult(`invalid glyph structure: ${issues}`);
      }
      // (b) char/codepoint/filename agreement
      const cp = char.codePointAt(0);
      if (glyph.char !== char)
        return errResult(`glyph.char "${glyph.char}" != requested char "${char}"`);
      if (glyph.codepoint !== cp)
        return errResult(
          `cp mismatch: glyph.codepoint=${glyph.codepoint} but "${char}" is U+${cp
            .toString(16)
            .padStart(4, "0")
            .toUpperCase()} (${cp})`,
        );

      const { font: fontSrc } = await getFont(font);

      // (c) semantic gate: it must rasterize
      try {
        await rasterOf(fontSrc, glyph, 60);
      } catch (e) {
        return errResult(`glyph does not rasterize: ${e.message}`);
      }

      // provenance guard + diff vs current on-disk glyph
      const file = glyphFile(font, cp);
      let before = null;
      if (fs.existsSync(file)) {
        try {
          before = JSON.parse(await fsp.readFile(file, "utf-8"));
        } catch {}
      }
      if (before && before.provenance === "edited" && !force) {
        return errResult(
          `refusing to overwrite hand-edited glyph "${char}" (provenance=edited). Pass force=true to override.`,
        );
      }
      const diff = {
        isNew: before === null,
        strokes: {
          before: before ? (before.strokes || []).length : 0,
          after: (glyph.strokes || []).length,
        },
        nodes: {
          before: before ? countNodes(before) : 0,
          after: countNodes(glyph),
        },
        provenance: {
          before: before ? before.provenance : null,
          after: glyph.provenance,
        },
      };
      const diffText =
        `${diff.isNew ? "NEW glyph" : "OVERWRITE"} "${char}" — ` +
        `strokes ${diff.strokes.before}->${diff.strokes.after}, ` +
        `nodes ${diff.nodes.before}->${diff.nodes.after}, ` +
        `provenance ${diff.provenance.before ?? "-"}->${diff.provenance.after}`;

      if (dryRun) {
        const r = await rasterOf(fontSrc, glyph, 60);
        return {
          content: [
            { type: "text", text: `DRY RUN (no write). Valid. ${diffText}` },
            imgBlock(r.png_b64),
          ],
          structuredContent: { written: false, dryRun: true, ...diff },
        };
      }

      const body = JSON.stringify(glyph, null, 1) + "\n";
      await atomicWrite(file, body);
      const r = await rasterOf(fontSrc, glyph, 60);
      return {
        content: [
          { type: "text", text: `WROTE ${file} (${body.length} bytes). ${diffText}` },
          imgBlock(r.png_b64),
        ],
        structuredContent: {
          written: true,
          path: file,
          bytes: body.length,
          ...diff,
        },
      };
    } catch (e) {
      return errResult(e.message);
    }
  },
);

server.registerTool(
  "compile_font",
  {
    title: "Compile font + self-check",
    description:
      "Compile the font to the BitmapFont .glyphs.npz contract, run the self-check (cap_h, advance_ratio, coverage, cap-height/clamp warnings), and return the contact-sheet PNG. Slow (up to ~180s).",
    inputSchema: { font: z.string() },
  },
  async ({ font }) => {
    try {
      const r = await compileFont(font);
      const coverage = Object.keys(r.glyphs || {}).length;
      const content = [{ type: "text", text: r.log.trim() }];
      try {
        const b64 = await pngFileToB64(r.sheetPath, 1000);
        content.push(imgBlock(b64));
      } catch (e) {
        content.push({
          type: "text",
          text: `(sheet render unavailable: ${e.message})`,
        });
      }
      return {
        content,
        structuredContent: { font, glyphCount: coverage, sheet: r.sheet },
      };
    } catch (e) {
      return errResult(e.message);
    }
  },
);

server.registerTool(
  "review_font",
  {
    title: "Review font on a real receipt",
    description:
      "Re-render a real reference receipt with the compiled font (BITMATRIX overlay) and score it. Returns the scorecard summary, per-glyph failures, and the review PNG. Requires a compiled font and a font.json review block. Slow (up to ~300s).",
    inputSchema: { font: z.string() },
  },
  async ({ font }) => {
    try {
      const { font: fontSrc } = await getFont(font);
      const review = fontSrc.review || {};
      if (!review.imageId)
        return errResult("font.json review block missing (need review.imageId)");
      const r = await reviewFont(font, review);
      const content = [
        {
          type: "text",
          text: `Review "${font}"\nsummary: ${JSON.stringify(r.summary)}\nfailures: ${r.failures.length}`,
        },
      ];
      try {
        const b64 = await pngFileToB64(r.pngPath, 1000);
        content.push(imgBlock(b64));
      } catch (e) {
        content.push({
          type: "text",
          text: `(review render unavailable: ${e.message})`,
        });
      }
      return {
        content,
        structuredContent: {
          font,
          summary: r.summary,
          failures: r.failures,
        },
      };
    } catch (e) {
      return errResult(e.message);
    }
  },
);

server.registerTool(
  "simplify_glyphs",
  {
    title: "Simplify glyph skeletons",
    description:
      "Run the stroke-consolidation engine (glyphstudio.simplify) over a font's glyphs to merge fragments and drop noise. apply=false previews only; apply=true writes. Returns per-glyph node counts before/after and which gates passed.",
    inputSchema: {
      font: z.string(),
      chars: z.string().optional(),
      apply: z.boolean().default(false),
    },
  },
  async ({ font, chars, apply }) => {
    const args = ["-m", "glyphstudio.simplify", fontPaths(font).dir];
    if (chars) args.push("--chars", chars);
    if (apply) args.push("--apply");
    args.push("--json");
    try {
      const { stdout } = await execFileP(PYTHON, args, {
        cwd: path.join(STUDIO_ROOT, "py"),
        // fidelity gate must judge against THIS font's corpus, not the default
        env: SAMPLES[font]
          ? { ...PY_ENV, GLYPHSTUDIO_SAMPLES: SAMPLES[font] }
          : PY_ENV,
        timeout: 300000,
        maxBuffer: 64 * 1024 * 1024,
      });
      let data;
      try {
        data = JSON.parse(stdout);
      } catch {
        return {
          content: [{ type: "text", text: stdout.trim() || "(no output)" }],
        };
      }
      return {
        content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
        structuredContent: data,
      };
    } catch (e) {
      // module not built yet, or engine error — surface stderr cleanly.
      const detail = e.stderr?.toString?.() || e.message;
      return errResult(`simplify engine failed: ${detail}`);
    }
  },
);

server.registerTool(
  "font_audit",
  {
    title: "Audit glyph anatomy",
    description:
      "Per-glyph anatomy computed from the sources: stroke/node counts, line vs cubic segment counts, closed loops, width, ascender/descender flags, and effective weight. Use it to find over-fragmented or malformed glyphs.",
    inputSchema: { font: z.string() },
  },
  async ({ font }) => {
    try {
      const { font: fontSrc, glyphs } = await getFont(font);
      const rows = Object.values(glyphs)
        .sort((a, b) => a.codepoint - b.codepoint)
        .map((g) => auditGlyph(g, fontSrc));
      const text = rows
        .map(
          (r) =>
            `${r.char}  strokes=${r.strokes} nodes=${r.nodes} line=${r.lineSegs} cubic=${r.cubicSegs} loops=${r.closedLoops}${
              r.descender ? " desc" : ""
            }${r.ascender ? " asc" : ""}`,
        )
        .join("\n");
      return {
        content: [
          { type: "text", text: `Anatomy for ${rows.length} glyphs in "${font}"\n${text}` },
        ],
        structuredContent: { font, count: rows.length, glyphs: rows },
      };
    } catch (e) {
      return errResult(e.message);
    }
  },
);

server.registerTool(
  "measure_glyph",
  {
    title: "Measure real-print consensus geometry",
    description:
      "Numerically measure a char's real-print consensus (the numbers an author places centerlines from): ink bbox in cap units, per-height ink spans, horizontal crossbars, vertical stems, stroke width, and hole count. Fast (persistent worker). Returns {available:false} when the corpus lacks the char.",
    inputSchema: {
      font: z.string(),
      char: z.string().min(1),
      threshold: z.number().default(0.45),
    },
  },
  async ({ font, char, threshold }) => {
    try {
      const samples = SAMPLES[font];
      if (!samples) return errResult(`no sample corpus configured for "${font}"`);
      const r = await pyCall("measure_char", { samples, char, threshold });
      // strip the worker envelope (id/ok) — keep just the measurement
      const { id, ok, ...measurement } = r;
      if (!measurement.available) {
        return {
          content: [
            { type: "text", text: `char "${char}" not in "${font}" corpus (available:false)` },
          ],
          structuredContent: measurement,
        };
      }
      const bb = measurement.ink_bbox || {};
      return {
        content: [
          {
            type: "text",
            text:
              `measure "${char}" (${font}, ${measurement.samples} samples, threshold=${threshold})\n` +
              `ink_bbox: y ${bb.y_bottom}..${bb.y_top}, x ${bb.x_left}..${bb.x_right} (cap units)\n` +
              `stroke_width=${measurement.stroke_width_units ?? "?"}u  bars=${(measurement.horizontal_bars || []).length}  stems=${(measurement.vertical_stems || []).length}  holes=${measurement.holes}`,
          },
        ],
        structuredContent: measurement,
      };
    } catch (e) {
      return errResult(e.message);
    }
  },
);

server.registerTool(
  "compare_glyph",
  {
    title: "Compare compiled glyphs vs consensus",
    description:
      "The confirmation arbiter view: run the batch compare CLI over one or more chars and return the magnified strip PNG. Each row is [soft consensus | compiled | overlay] so misalignment and weight mismatch read instantly against the real prints.",
    inputSchema: { font: z.string(), chars: z.string().min(1) },
  },
  async ({ font, chars }) => {
    try {
      const samples = SAMPLES[font];
      if (!samples) return errResult(`no sample corpus configured for "${font}"`);
      const out = path.join(OUT_DIR, `mcp-compare-${tsStamp()}.png`);
      // execFile (no shell) — `chars` may hold shell-special characters; passed
      // through as a single argv element, so no quoting/escaping is needed.
      const { stdout } = await execFileP(
        PYTHON,
        [
          "-m",
          "glyphstudio.compare",
          samples,
          fontPaths(font).dir,
          out,
          "--chars",
          chars,
          "--scale",
          "2",
        ],
        {
          cwd: path.join(STUDIO_ROOT, "py"),
          env: PY_ENV,
          timeout: 180000,
          maxBuffer: 64 * 1024 * 1024,
        },
      );
      const b64 = await pngFileToB64(out, 1000);
      return {
        content: [
          imgBlock(b64),
          {
            type: "text",
            text: `compare "${chars}" — columns [soft consensus | compiled | overlay]\n${stdout.trim()}`,
          },
        ],
      };
    } catch (e) {
      const detail = e.stderr?.toString?.() || e.message;
      return errResult(`compare failed: ${detail}`);
    }
  },
);

server.registerTool(
  "publish_font",
  {
    title: "Publish font to the global BITMATRIX_DIR",
    description:
      "Encode the publish ritual: compile (abort unless every source glyph makes it into the atlas), timestamp-backup the existing $BITMATRIX_DIR/<font>.glyphs.npz, remove it first if it is a symlink (macOS cp writes THROUGH symlinks — this once corrupted the global dir), copy the fresh .out npz over, then clear the *inkthin* render-cache pickles (optionally seeding thinSeed) so the new font takes effect. Slow (compiles).",
    inputSchema: {
      font: z.string(),
      thinSeed: z.number().optional(),
    },
  },
  async ({ font, thinSeed }) => {
    try {
      // (a) compile + full-coverage gate: every source glyph must land in the
      //     atlas (an empty raster drops a glyph — refuse to publish that).
      const { glyphs: sourceGlyphs } = await getFont(font);
      const sourceCount = Object.keys(sourceGlyphs).length;
      const r = await compileFont(font);
      const compiledCount = Object.keys(r.glyphs || {}).length;
      if (compiledCount < sourceCount) {
        return errResult(
          `coverage not full: only ${compiledCount}/${sourceCount} source glyphs compiled ` +
            `(some produced empty rasters) — aborting publish.\n${r.log.trim()}`,
        );
      }

      const target = path.join(BITMATRIX_DIR, `${font}.glyphs.npz`);
      await fsp.mkdir(BITMATRIX_DIR, { recursive: true });

      // (b) timestamped backup of the current published npz (follows a symlink
      //     to capture the real bytes) before we touch anything.
      let backup = null;
      if (fs.existsSync(target)) {
        backup = `${target}.bak-${tsStamp()}`;
        await fsp.copyFile(target, backup);
      }

      // (c) if the target is a SYMLINK, remove it first — macOS cp/copyFile
      //     writes THROUGH a symlink into whatever it points at.
      let removedSymlink = false;
      try {
        const st = await fsp.lstat(target);
        if (st.isSymbolicLink()) {
          await fsp.rm(target, { force: true });
          removedSymlink = true;
        }
      } catch {}

      // (d) copy the fresh compiled npz over the target
      await fsp.copyFile(r.npz, target);

      // (e) clear the *inkthin* render-cache pickles so the new font is picked
      //     up, then optionally seed a fixed thin value.
      const cleared = [];
      try {
        for (const f of await fsp.readdir(RENDER_CACHE_DIR)) {
          if (f.includes("inkthin")) {
            await fsp.rm(path.join(RENDER_CACHE_DIR, f), { force: true });
            cleared.push(f);
          }
        }
      } catch {}
      let seedWritten = null;
      if (typeof thinSeed === "number") {
        // The renderer keys its inkthin pickle off the CANONICAL merchant name
        // (see _render_cache_path), not the font dir name — resolve it so we
        // seed the file the renderer will actually read.
        const merchant = FONT_MERCHANTS[font];
        if (!merchant) {
          throw new Error(
            `no merchant mapping for font "${font}" — cannot target its ` +
              "inkthin render cache; add it to FONT_MERCHANTS in env.mjs",
          );
        }
        await fsp.mkdir(RENDER_CACHE_DIR, { recursive: true });
        const seedFile = path.join(
          RENDER_CACHE_DIR,
          `${renderCacheSlug(merchant)}__inkthin__n1.pkl`,
        );
        await execFileP(
          PYTHON,
          [
            "-c",
            "import sys, pickle; pickle.dump(float(sys.argv[2]), open(sys.argv[1], 'wb'))",
            seedFile,
            String(thinSeed),
          ],
          { env: PY_ENV, timeout: 30000 },
        );
        seedWritten = { path: seedFile, value: thinSeed };
      }

      const lines = [
        `Published "${font}": ${compiledCount}/${sourceCount} glyphs -> ${target}`,
        backup ? `backup: ${backup}` : "(no prior file to back up)",
        removedSymlink ? "(removed symlink before copy)" : null,
        `cache cleared: ${cleared.join(", ") || "-"}`,
        seedWritten ? `thinSeed=${thinSeed} written to ${seedWritten.path}` : null,
      ].filter(Boolean);
      return {
        content: [{ type: "text", text: lines.join("\n") }],
        structuredContent: {
          font,
          published: target,
          npzSource: r.npz,
          backup,
          removedSymlink,
          compiledCount,
          sourceCount,
          cache: { cleared, seedWritten },
        },
      };
    } catch (e) {
      return errResult(e.message);
    }
  },
);

// ---------------------------------------------------------------------------
async function main() {
  
// ---- receipt_logo integration (PR #1033 package; degrades gracefully until merged)
const LOGO_PKG = path.join(STUDIO_ROOT, "..", "..", "receipt_logo");
const LOGO_ASSETS = path.join(LOGO_PKG, "receipt_logo", "assets", "merchant_logos");

server.registerTool(
  "list_logos",
  {
    title: "List merchant logo path assets",
    description:
      "Merchant logo SVG assets managed by the receipt_logo package (PNG-to-SVG vectorized or authored vector sources), with their manifests (palette, dimensions, path counts).",
    inputSchema: {},
  },
  async () => {
    try {
      const entries = await fsp.readdir(LOGO_ASSETS).catch(() => null);
      if (!entries)
        return errResult(
          "receipt_logo package not present in this worktree (lands with PR #1033)",
        );
      const manifests = [];
      for (const f of entries.filter((e) => e.endsWith(".manifest.json"))) {
        manifests.push(JSON.parse(await fsp.readFile(path.join(LOGO_ASSETS, f), "utf8")));
      }
      const text = manifests
        .map((m) => `${m.merchant_slug}: ${m.assets.color_svg} palette=${(m.palette || []).join(",")}`)
        .join("\n");
      return {
        content: [{ type: "text", text: text || "no logo assets" }],
        structuredContent: { logos: manifests },
      };
    } catch (e) {
      return errResult(e.message);
    }
  },
);

server.registerTool(
  "vectorize_logo",
  {
    title: "Vectorize a merchant logo PNG to SVG paths",
    description:
      "Run the receipt_logo deterministic PNG-to-SVG vectorizer on an image file and write the SVG + manifest into the package's assets. Authored vector sources (SVGs) should be copied in directly instead.",
    inputSchema: {
      image_path: z.string(),
      merchant_slug: z.string().regex(/^[a-z0-9_]+$/),
    },
  },
  async ({ image_path, merchant_slug }) => {
    try {
      const cliOk = await fsp.stat(path.join(LOGO_PKG, "receipt_logo", "cli.py")).catch(() => null);
      if (!cliOk)
        return errResult(
          "receipt_logo package not present in this worktree (lands with PR #1033)",
        );
      const { stdout, stderr } = await execFileP(
        PYTHON,
        ["-m", "receipt_logo.cli", "vectorize", image_path, "--merchant-slug", merchant_slug],
        { cwd: LOGO_PKG, env: PY_ENV, timeout: 120000 },
      );
      return {
        content: [{ type: "text", text: (stdout || stderr || "done").slice(0, 4000) }],
      };
    } catch (e) {
      return errResult(e.message);
    }
  },
);

const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("glyph-studio MCP server ready (stdio)");
}

// On shutdown, exiting closes the python worker's stdin pipe -> its
// `for line in sys.stdin` loop hits EOF and the worker exits cleanly.
for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => process.exit(0));
}

main().catch((e) => {
  console.error("fatal:", e);
  process.exit(1);
});
