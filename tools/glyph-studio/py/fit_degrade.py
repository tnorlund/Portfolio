#!/usr/bin/env python3
"""fit_degrade.py -- fit the I1 degradation params against REAL Costco evidence.

NO hand-tuned constants: the 17-parameter print+scan chain in
``glyphstudio.degrade`` is fitted here with CMA-ES against a TRAIN split of
real Costco receipts; the gold pair the ladder scores (57cb7f2c real.webp)
stays HELD OUT of the fit.

Train split (documented in the output JSON):
- Candidates: the glyphscore per-receipt crop cache (vetted extraction:
  OCR-overlap <= 2, >= 150 letters), EXCLUDING the gold receipt.
- Class vetting: keep auto-leveled scans (paper_clip_frac > 0.3), the scan
  class the gold pipeline targets. Images are resized to the 760 px render
  width so texture statistics are scale-comparable.
- Targets: median stroke-core L / CoV / edge-transition frac / clip frac +
  the pooled body tone histogram (EMD reference) over the train scans;
  pooled per-char crop masks (train refpack) for the Kanungo 3x3
  pattern-histogram distance and the GlyphScore percentile.

Objective (GOLD_STANDARD.md ladder terms, tolerance-normalized):
- L0 ink:   normalized glyph fill vs train fill
- L3 texture: stroke L, stroke CoV, edge frac, clip frac, tone EMD,
  Kanungo pattern-hist distance (two-sample statistic vs train crops)
- L1:       GlyphScore percentile vs train crops -> 50 (hinge below 45)

Fitted in stage groups for identifiability (research_printer.md s7):
  A geometry/tone/scan (noise off) -> B noise texture (A frozen) -> C joint
  polish. Every evaluation is logged to a JSONL trace.

Usage:
    fit_degrade.py --render final_i2.webp --labels final.labels.json \
        --out degrade_params/costco.degrade.json [--smoke]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from collections import defaultdict

import numpy as np
from PIL import Image
from scipy import ndimage

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))

# degrade is loaded standalone (by file path) so the ``glyphstudio`` package
# name can resolve entirely to the glyphscore worktree (same convention as
# costco_gold.py) -- importing our repo's glyphstudio would shadow
# glyphstudio.glyph_score there.
_dspec = importlib.util.spec_from_file_location(
    "gs_degrade", os.path.join(_HERE, "glyphstudio", "degrade.py"))
_dmod = importlib.util.module_from_spec(_dspec)
_dspec.loader.exec_module(_dmod)
PARAM_BOUNDS, PARAM_ORDER, degrade = (
    _dmod.PARAM_BOUNDS, _dmod.PARAM_ORDER, _dmod.degrade)

GOLD_IMAGE_ID = "57cb7f2c-7dcc-4974-9ef8-a460232f3b1d"
BODY = (0.167, 0.617)  # gold-pair body band (harness regions config)
TRAIN_BAND = (0.20, 0.65)  # generic body band for train scans
DARK_THRESH = 160.0
MIN_CLIP = 0.3  # auto-leveled-scan class gate for train scans
NOISE_PARAMS = (
    "jitter_sigma", "jitter_corr", "mottle_sigma", "mottle_scale",
    "k_a0", "k_decay", "k_b0", "k_eta", "sensor_sigma",
)
STAGE_A_PARAMS = tuple(k for k in PARAM_ORDER if k not in NOISE_PARAMS)


def _log(msg: str) -> None:
    print(f"[fit_degrade {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _load_gold_metrics(harness_root: str):
    path = os.path.join(harness_root, "glyphstudio", "gold_metrics.py")
    spec = importlib.util.spec_from_file_location("gold_metrics", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_glyphscore(root: str):
    for p in (os.path.join(root, "tools/glyph-studio/py"),
              os.path.join(root, "synthesis_loop")):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    import glyph_score_cli as gsc  # noqa: E402
    from glyphstudio.glyph_score import (  # noqa: E402
        build_self_references, score_instances, weighted_glyphscore,
    )
    return gsc, build_self_references, score_instances, weighted_glyphscore


# --- train split assembly -------------------------------------------------


def _train_candidates(cache_dir: str) -> list[tuple[str, int, str]]:
    out = []
    for f in sorted(os.listdir(cache_dir)):
        if not f.startswith("costco_") or not f.endswith(".npz"):
            continue
        stem = f[len("costco_"):-len(".npz")]
        iid, rid = stem.rsplit("_", 1)
        if iid == GOLD_IMAGE_ID:
            continue
        d = np.load(os.path.join(cache_dir, f), allow_pickle=False)
        meta = json.loads(str(d["meta"]))
        if meta.get("ocr_overlap_score", 99) > 2:
            continue
        if meta.get("n_letters", 0) < 150:
            continue
        out.append((iid, int(rid), os.path.join(cache_dir, f)))
    return out


def _fetch_train_image(iid: str, rid: int, img_dir: str):
    png = os.path.join(img_dir, f"{iid}_{rid}.png")
    if os.path.exists(png):
        return Image.open(png)
    for p in (os.path.join(_ROOT, "receipt_dynamo"),
              os.path.join(_ROOT, "receipt_upload"),
              "/Users/tnorlund/Portfolio/synthesis_loop"):
        if p not in sys.path:
            sys.path.insert(0, p)
    from receipt_line_scorecard import _load_words_and_real  # noqa: E402

    img, _words = _load_words_and_real("Costco Wholesale", iid, rid)
    w, h = img.size
    img = img.convert("L").resize(
        (760, int(round(760 * h / w))), Image.LANCZOS
    )
    os.makedirs(img_dir, exist_ok=True)
    img.save(png)
    return img


def _speck_density(ink: np.ndarray) -> float:
    """Isolated small ink components away from text ink, per 10k px.

    The visual/forensic 'dirty paper' tell: real auto-leveled scans have
    nearly clean paper (~1/10k), while an unconstrained noise fit peppers
    it (round-1 lesson: 22/10k)."""
    lab, n = ndimage.label(ink)
    if n == 0:
        return 0.0
    sizes = ndimage.sum(ink, lab, range(1, n + 1))
    big = np.isin(lab, np.where(sizes >= 12)[0] + 1)
    near_text = ndimage.binary_dilation(big, iterations=3)
    specks = ink & ~big & ~near_text
    return float(specks.sum() / specks.size * 1e4)


def _texture_stats(gm, gray: np.ndarray, band: tuple[float, float]) -> dict:
    H = gray.shape[0]
    body = gray[int(band[0] * H): int(band[1] * H)]
    ink = body < DARK_THRESH
    st = gm.stroke_core_stats(body, ink, erode=1)
    zone = ndimage.binary_dilation(ink, iterations=2)
    hist, _ = np.histogram(body, bins=64, range=(0, 255), density=True)
    return {
        "median_L": st["median_L"],
        "cov": st["cov"],
        "edge": gm.edge_transition_frac(body, zone),
        "clip": gm.paper_clip_frac(body),
        # mean darkness over ALL thresholded ink px (edges included): the
        # whole-stroke ink budget, which the round-1 fit under-delivered
        # (blur spread ink too thin at matched core darkness).
        "ink_mean": float((255.0 - body[ink]).mean()) if ink.any() else float("nan"),
        "speck": _speck_density(ink),
        "hist": hist,
    }


def build_train_targets(gm, cache_dir: str, img_dir: str) -> dict:
    """Vetted train scans -> texture targets + pooled crop pack."""
    receipts, stats, hists = [], [], []
    pack: dict[str, list[np.ndarray]] = defaultdict(list)
    for iid, rid, npz in _train_candidates(cache_dir):
        try:
            img = _fetch_train_image(iid, rid, img_dir)
        except Exception as e:  # noqa: BLE001
            _log(f"  [skip] {iid[:8]}#{rid}: {e}")
            continue
        gray = np.asarray(img.convert("L"), np.float64)
        s = _texture_stats(gm, gray, TRAIN_BAND)
        if s["clip"] <= MIN_CLIP:
            _log(f"  [class-out] {iid[:8]}#{rid}: clip={s['clip']:.3f}"
                 f" (not an auto-leveled scan)")
            continue
        receipts.append(f"{iid}#{rid}")
        stats.append(s)
        hists.append(s["hist"])
        d = np.load(npz, allow_pickle=False)
        for ch, m in zip([str(c) for c in d["chars"]], d["masks"].astype(bool)):
            pack[ch].append(m)
        _log(f"  [train] {iid[:8]}#{rid}: L={s['median_L']:.1f}"
             f" cov={s['cov']:.3f} edge={s['edge']:.3f} clip={s['clip']:.3f}")
    if len(receipts) < 3:
        raise SystemExit("fewer than 3 vetted train scans; refusing to fit")
    med = lambda k: float(np.median([s[k] for s in stats]))  # noqa: E731

    def iqr(k):
        v = [s[k] for s in stats]
        return (float(np.percentile(v, 25)), float(np.percentile(v, 75)))

    stacks = {ch: np.stack(ms) for ch, ms in pack.items()}
    return {
        "receipts": receipts,
        "median_L": med("median_L"),
        "cov": med("cov"),
        "edge": med("edge"),
        "clip": med("clip"),
        "ink_mean": med("ink_mean"),
        "speck": med("speck"),
        # in-family intervals: any value inside a stat's train IQR is a valid
        # member of the print/scan family (the gold scan is ONE draw of it);
        # the objective only penalizes leaving the family.
        "iqr": {k: iqr(k) for k in
                ("median_L", "cov", "edge", "clip", "ink_mean", "speck")},
        "hist": np.mean(np.stack(hists), axis=0),
        "fill": float(np.median(
            [float(m.mean()) for ms in stacks.values() for m in ms]
        )),
        "pack": stacks,
    }


# --- objective ---------------------------------------------------------------


def _hist_emd(hist_a: np.ndarray, hist_b: np.ndarray) -> float:
    ca = np.cumsum(hist_a) / max(hist_a.sum(), 1e-12)
    cb = np.cumsum(hist_b) / max(hist_b.sum(), 1e-12)
    return float(np.sum(np.abs(ca - cb)) * (255.0 / 64))


class FitContext:
    """Precomputed data + one-call objective evaluation."""

    def __init__(self, args):
        self.gm = _load_gold_metrics(args.harness_root)
        (self.gsc, build_refs, self.score_instances,
         self.weighted_glyphscore) = _import_glyphscore(args.glyphscore_root)
        self.gray = np.asarray(
            Image.open(args.render).convert("L"), np.float64
        )
        self.H, self.W = self.gray.shape
        self.y0, self.y1 = int(BODY[0] * self.H), int(BODY[1] * self.H)

        _log("assembling train split ...")
        self.train = build_train_targets(
            self.gm, args.cache_dir, args.train_img_dir
        )
        _log(
            f"train targets: L={self.train['median_L']:.1f} "
            f"cov={self.train['cov']:.3f} edge={self.train['edge']:.3f} "
            f"clip={self.train['clip']:.3f} fill={self.train['fill']:.4f} "
            f"({len(self.train['receipts'])} receipts)"
        )
        _log("building GlyphScore self-references from the train pack ...")
        self.refs = build_refs(self.train["pack"])

        # Texture anchor. 'train' (default) keeps the gold scan fully held
        # out; rounds 1-3 measured that the gold scan sits at the CLEAN end
        # of the train family for the SCAN-stage stats (edge softness, paper
        # clip, tone) -- scanner properties, not print physics -- so strict
        # train anchoring cannot land inside the ladder's dynamic +-tol bands
        # around the gold draw. 'gold' anchors the texture point-targets on
        # the gold scan per GOLD_STANDARD I1 ("vetted crops + the gold scan's
        # texture statistics"); the CROP-level guards (fill / Kanungo /
        # GlyphScore) stay train-anchored in both modes.
        self.anchor = args.texture_anchor
        self.gold = None
        if self.anchor == "gold":
            gold_gray = np.asarray(
                Image.open(args.gold_real).convert("L"), np.float64)
            self.gold = _texture_stats(self.gm, gold_gray, BODY)
            self.gold["ink_total"] = float((255.0 - gold_gray).sum())
            _log(f"gold texture anchor: L={self.gold['median_L']:.1f} "
                 f"cov={self.gold['cov']:.3f} edge={self.gold['edge']:.3f} "
                 f"clip={self.gold['clip']:.3f} speck={self.gold['speck']:.2f}")

        # body-band word subset (segmentation cost control; hygiene-free zone)
        with open(args.labels, encoding="utf-8") as fh:
            lab = json.load(fh)
        words = []
        for t, b in zip(lab["tokens"], lab["bboxes"]):
            y_top_frac = 1.0 - max(b[1], b[3]) / 1000.0
            y_bot_frac = 1.0 - min(b[1], b[3]) / 1000.0
            if y_top_frac >= BODY[0] and y_bot_frac <= BODY[1]:
                words.append({"text": t, "bbox": list(b[:4])})
        self.words = words[: args.max_words]
        _log(f"fit segmentation subset: {len(self.words)} body tokens")
        self.n_eval = 0
        self.trace: list[dict] = []

    def evaluate(self, params: dict, *, with_glyphs: bool = True) -> tuple[float, dict]:
        t0 = time.time()
        deg = degrade(self.gray, params, seed=0)
        body = deg[self.y0: self.y1]
        ink = body < DARK_THRESH
        st = self.gm.stroke_core_stats(body, ink, erode=1)
        zone = ndimage.binary_dilation(ink, iterations=2)
        hist, _ = np.histogram(body, bins=64, range=(0, 255), density=True)
        tr = self.train

        def z_iqr(value, key, tol):
            lo, hi = tr["iqr"][key]
            if lo <= value <= hi:
                return 0.0
            return (value - hi if value > hi else value - lo) / tol

        ink_mean = float((255.0 - body[ink]).mean()) if ink.any() else float("nan")
        if self.anchor == "gold":
            g = self.gold
            terms = {
                "z_L": (st["median_L"] - g["median_L"]) / 6.0,
                "z_cov": (st["cov"] - g["cov"]) / 0.06,
                "z_edge": (self.gm.edge_transition_frac(body, zone) - g["edge"]) / 0.05,
                "z_clip": (self.gm.paper_clip_frac(body) - g["clip"]) / 0.10,
                "z_inkmean": (ink_mean - g["ink_mean"]) / 6.0,
                "z_speck": (_speck_density(ink) - g["speck"]) / 1.0,
                "z_emd": _hist_emd(g["hist"], hist) / 3.0,
                # the ladder's L0 total-ink gate (same content, whole canvas)
                "z_ink": ((255.0 - deg).sum() / g["ink_total"] - 1.0) / 0.05,
            }
        else:
            terms = {
                "z_L": z_iqr(st["median_L"], "median_L", 6.0),
                "z_cov": z_iqr(st["cov"], "cov", 0.06),
                "z_edge": z_iqr(self.gm.edge_transition_frac(body, zone), "edge", 0.05),
                "z_clip": z_iqr(self.gm.paper_clip_frac(body), "clip", 0.10),
                "z_inkmean": z_iqr(ink_mean, "ink_mean", 6.0),
                "z_speck": z_iqr(_speck_density(ink), "speck", 1.0),
                "z_emd": _hist_emd(tr["hist"], hist) / 3.0,
            }
        gs = fill = kan = float("nan")
        if with_glyphs:
            img = Image.fromarray(np.round(deg).astype(np.uint8), "L")
            instances, _seg = self.gsc.segment_render(img, self.words)
            masks = [np.asarray(m, bool) for _c, m, _l in instances]
            fill = self.gm.normalized_fill(masks)
            terms["z_fill"] = (fill - tr["fill"]) / 0.01
            # kanungo: char-matched subsample, distance only (p-value at eval)
            by_char = defaultdict(list)
            for ch, m, _l in instances:
                by_char[ch].append(np.asarray(m, bool))
            rc, xc = [], []
            for ch, ms in by_char.items():
                stk = tr["pack"].get(ch)
                if stk is None:
                    continue
                k = min(8, len(ms), stk.shape[0])
                if k < 1:
                    continue
                idx = np.linspace(0, stk.shape[0] - 1, k).round().astype(int)
                rc.extend(ms[:k])
                xc.extend(stk[i] for i in np.unique(idx))
            kan = self.gm.kanungo_two_sample(rc, xc, n_perm=0)["distance"]
            terms["z_kan"] = kan / 0.02
            res = self.score_instances(self.refs, instances, max_shift=2)
            gs = self.weighted_glyphscore(res)
            terms["z_gs"] = max(0.0, 50.0 - gs) / 5.0
        loss = float(sum(v * v for v in terms.values() if np.isfinite(v)))
        self.n_eval += 1
        rec = {
            "eval": self.n_eval,
            "loss": round(loss, 4),
            "terms": {k: round(v, 3) for k, v in terms.items()},
            "gs": None if not np.isfinite(gs) else round(float(gs), 2),
            "fill": None if not np.isfinite(fill) else round(float(fill), 4),
            "kan": None if not np.isfinite(kan) else round(float(kan), 4),
            "sec": round(time.time() - t0, 2),
        }
        self.trace.append({**rec, "params": {k: round(float(params[k]), 5)
                                             for k in PARAM_ORDER}})
        return loss, rec


# --- CMA-ES staging -----------------------------------------------------------


def _to_unit(params: dict, keys) -> np.ndarray:
    x = []
    for k in keys:
        lo, hi = PARAM_BOUNDS[k]
        x.append((params[k] - lo) / (hi - lo))
    return np.clip(np.asarray(x, float), 0.0, 1.0)


def _from_unit(x: np.ndarray, keys, base: dict) -> dict:
    p = dict(base)
    for v, k in zip(x, keys):
        lo, hi = PARAM_BOUNDS[k]
        p[k] = lo + float(np.clip(v, 0.0, 1.0)) * (hi - lo)
    return p


def _stage(ctx, base: dict, keys, *, label: str, iters: int, popsize: int,
           sigma0: float, with_glyphs: bool, seed: int) -> dict:
    import cma

    _log(f"stage {label}: {len(keys)} params, {iters} iters x pop {popsize}")
    es = cma.CMAEvolutionStrategy(
        _to_unit(base, keys), sigma0,
        {"popsize": popsize, "bounds": [0.0, 1.0], "seed": seed,
         "verbose": -9},
    )
    # Seed with the incoming point so a stage can never RETURN worse than it
    # received (CMA samples around base; none is guaranteed to beat it).
    base_loss, _ = ctx.evaluate(base, with_glyphs=with_glyphs)
    best = (base_loss, dict(base))
    for it in range(iters):
        xs = es.ask()
        losses = []
        for x in xs:
            p = _from_unit(np.asarray(x), keys, base)
            loss, rec = ctx.evaluate(p, with_glyphs=with_glyphs)
            losses.append(loss)
            if loss < best[0]:
                best = (loss, p)
        es.tell(xs, losses)
        _log(f"  {label} iter {it + 1}/{iters}: best={best[0]:.3f} "
             f"gen_min={min(losses):.3f} evals={ctx.n_eval}")
    return best[1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--render", required=True,
                    help="clean render to fit ON (the I2 render)")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--trace-out")
    ap.add_argument("--harness-root",
                    default=os.environ.get(
                        "GOLD_HARNESS_ROOT",
                        "/Users/tnorlund/Portfolio-gold-harness/tools/glyph-studio/py"))
    ap.add_argument("--glyphscore-root",
                    default=os.environ.get("GLYPHSCORE_ROOT",
                                           "/private/tmp/glyphscore"))
    ap.add_argument("--cache-dir",
                    default="/private/tmp/glyphscore/.out/glyphscore/cache/"
                            "ReceiptsTable-dc5be22")
    ap.add_argument("--train-img-dir",
                    default=os.path.join(_ROOT, ".out", "train_reals"))
    ap.add_argument("--max-words", type=int, default=120)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--iters-a", type=int, default=14)
    ap.add_argument("--iters-b", type=int, default=14)
    ap.add_argument("--iters-c", type=int, default=8)
    ap.add_argument("--popsize", type=int, default=10)
    ap.add_argument("--smoke", action="store_true",
                    help="3 evals + timing, no fit")
    ap.add_argument("--start",
                    help="warm-start params JSON (a prior fit round)")
    ap.add_argument("--texture-anchor", choices=("train", "gold"),
                    default="train",
                    help="texture point-targets: 'train' (gold fully held "
                         "out) or 'gold' (GOLD_STANDARD I1: the gold scan's "
                         "texture statistics; crop guards stay train-anchored)")
    ap.add_argument("--gold-real",
                    default="/Users/tnorlund/Portfolio/portfolio/public/"
                            "synthetic-receipts/pipeline/costco/real.webp")
    args = ap.parse_args(argv)

    ctx = FitContext(args)

    if args.start:
        with open(args.start, encoding="utf-8") as fh:
            start = json.load(fh)["params"]
        _log(f"warm start from {args.start}")
    else:
        # start: mid-bounds, noise off (stage A explores from a neutral chain)
        start = {}
        for k in PARAM_ORDER:
            lo, hi = PARAM_BOUNDS[k]
            start[k] = lo if k in NOISE_PARAMS else 0.5 * (lo + hi)
        start["white_point"] = 250.0

    if args.smoke:
        for i in range(3):
            loss, rec = ctx.evaluate(start)
            _log(f"smoke {i}: loss={loss:.3f} {rec}")
        return 0

    t0 = time.time()
    best = _stage(ctx, start, STAGE_A_PARAMS, label="A(geom/tone/scan)",
                  iters=args.iters_a, popsize=args.popsize, sigma0=0.3,
                  with_glyphs=True, seed=args.seed)
    best = _stage(ctx, best, NOISE_PARAMS, label="B(noise)",
                  iters=args.iters_b, popsize=args.popsize, sigma0=0.3,
                  with_glyphs=True, seed=args.seed + 1)
    best = _stage(ctx, best, PARAM_ORDER, label="C(joint polish)",
                  iters=args.iters_c, popsize=args.popsize, sigma0=0.12,
                  with_glyphs=True, seed=args.seed + 2)

    loss, rec = ctx.evaluate(best)
    _log(f"final: loss={loss:.3f} gs={rec['gs']} fill={rec['fill']} "
         f"kan={rec['kan']} ({ctx.n_eval} evals, "
         f"{round(time.time() - t0, 1)}s)")

    doc = {
        "merchant": "costco",
        "tool": "fit_degrade.py",
        "date": time.strftime("%Y-%m-%d"),
        "fitted_on_render": os.path.relpath(args.render, _ROOT)
        if args.render.startswith(_ROOT) else args.render,
        "texture_anchor": args.texture_anchor,
        "held_out": (
            f"gold pair {GOLD_IMAGE_ID}#1 (real.webp / ladder)"
            if args.texture_anchor == "train" else
            f"crop-level guards (fill/Kanungo/GlyphScore) train-anchored; "
            f"scan-stage texture stats anchored on the gold scan per "
            f"GOLD_STANDARD I1 (train-only transfer gap measured in "
            f"rounds 1-3)"),
        "train_receipts": ctx.train["receipts"],
        "train_targets": {
            "median_L": ctx.train["median_L"],
            "cov": ctx.train["cov"],
            "edge": ctx.train["edge"],
            "clip": ctx.train["clip"],
            "fill": ctx.train["fill"],
        },
        "objective_final": rec,
        "n_evals": ctx.n_eval,
        "seed": args.seed,
        "params": {k: round(float(best[k]), 6) for k in PARAM_ORDER},
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    _log(f"wrote {args.out}")
    trace_path = args.trace_out or (os.path.splitext(args.out)[0] + ".trace.jsonl")
    with open(trace_path, "w", encoding="utf-8") as fh:
        for r in ctx.trace:
            fh.write(json.dumps(r) + "\n")
    _log(f"wrote {trace_path} ({len(ctx.trace)} evals)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
