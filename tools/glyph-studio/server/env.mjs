// Central environment resolution for the studio server.
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const STUDIO_ROOT = path.resolve(HERE, "..");
export const WORKTREE = path.resolve(STUDIO_ROOT, "..", "..");
export const FONTS_DIR = path.join(STUDIO_ROOT, "fonts");
export const OUT_DIR = path.join(STUDIO_ROOT, ".out");
export const PYTHON =
  process.env.GLYPH_STUDIO_PYTHON ||
  path.join(os.homedir(), "Portfolio", ".venv", "bin", "python");

export const SAMPLES = {
  sprouts: "/tmp/gridfix/sprouts_demo/sprouts.samples.npz",
  costco: "/tmp/gridfix/costco_studio/costco-studio.samples.npz",
  vons: "/tmp/gridfix/vons_studio/vons-studio.samples.npz",
  traderjoes: "/tmp/gridfix/tj_studio/tj.refined.npz",
  cvs: "/tmp/gridfix/cvs_studio/cvs.refined.npz",
  wildfork: "/tmp/gridfix/wildfork_studio/wildfork.refined.npz",
  innout: "/tmp/gridfix/innout_studio/innout.refined.npz",
  target: "/tmp/gridfix/target_studio/target.refined.npz",
  homedepot: "/tmp/gridfix/homedepot_studio/homedepot.refined.npz",
};

// Font dir -> canonical merchant name. The receipt renderer caches per-merchant
// ink-erosion pickles under a slug of the CANONICAL merchant name (see
// scripts/render_synthetic_receipts.py::_render_cache_path). publish_font seeds
// that pickle, so it must resolve the font's merchant to hit the right file.
export const FONT_MERCHANTS = {
  sprouts: "Sprouts Farmers Market",
  costco: "Costco Wholesale",
  vons: "Vons",
  traderjoes: "Trader Joe's",
  cvs: "CVS",
  wildfork: "Wild Fork",
  innout: "In-N-Out Burger",
  homedepot: "The Home Depot",
};

// Mirror _render_cache_path's slug: runs of non-alphanumerics -> single "_".
export function renderCacheSlug(merchant) {
  return (merchant || "none").replace(/[^A-Za-z0-9]+/g, "_");
}

export const PY_ENV = {
  ...process.env,
  PYTHONPATH: [
    path.join(STUDIO_ROOT, "py"),
    path.join(WORKTREE, "receipt_agent"),
    path.join(WORKTREE, "receipt_dynamo"),
    path.join(WORKTREE, "receipt_upload"),
    path.join(WORKTREE, "receipt_chroma"),
    path.join(WORKTREE, "receipt_places"),
    path.join(WORKTREE, "receipt_dynamo_stream"),
    path.join(WORKTREE, "receipt_label"),
    path.join(WORKTREE, "scripts"),
    path.join(WORKTREE, "synthesis_loop"),
    process.env.PYTHONPATH || "",
  ].join(path.delimiter),
  DYNAMODB_TABLE_NAME: process.env.DYNAMODB_TABLE_NAME || "ReceiptsTable-dc5be22",
  AWS_REGION: process.env.AWS_REGION || "us-east-1",
  PORTFOLIO_ENV: process.env.PORTFOLIO_ENV || "dev",
  FONT_LIB: process.env.FONT_LIB || "/tmp/fonts_lib",
  RECEIPT_PAPER_STRENGTH: process.env.RECEIPT_PAPER_STRENGTH || "0.3",
};

export const BITMATRIX_DIR = process.env.BITMATRIX_DIR || "/tmp/bitmatrix";
// Where the receipt renderer caches per-merchant ink/atlas pickles. publish_font
// clears the *inkthin* pickles here so a fresh font takes effect. Overridable so
// tests stay hermetic (default is the real global cache).
export const RENDER_CACHE_DIR =
  process.env.RENDER_CACHE_DIR || "/tmp/render_cache";
export const PORT = Number(process.env.GLYPH_STUDIO_PORT || 5177);
