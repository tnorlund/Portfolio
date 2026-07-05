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
  PYTHON,
  PY_ENV,
  STUDIO_ROOT,
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
      "Render the real-letterform corpus for a char: median (soft consensus), binary (thresholded consensus), or index (a single sample i). Compare a candidate against the ground-truth ink the tracer saw.",
    inputSchema: {
      font: z.string(),
      char: z.string().min(1),
      mode: z.enum(["median", "binary", "index"]).default("median"),
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
        env: PY_ENV,
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

// ---------------------------------------------------------------------------
async function main() {
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
