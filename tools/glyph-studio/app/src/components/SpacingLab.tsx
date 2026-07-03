import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../store";
import { api } from "../api";
import type { AtlasGlyph } from "../types";
import {
  capHeight,
  advanceRatio,
  cellWidth,
  scaledGlyph,
  renderHeight,
  nearestResize,
  rowsToBits,
  unpackBits,
  thinInkMask,
  pyRound,
  type Atlas,
} from "../cellMath";

const DEFAULT_LINES = ["SPROUTS FARMERS MARKET", "ORG A2/A2 6% FAT MLK 7.99"];

function bitsForGlyph(g: AtlasGlyph): Uint8Array | null {
  if (g.bits_b64) return unpackBits(g.bits_b64, g.w, g.h);
  if (g.rows) return rowsToBits(g.rows);
  return null;
}

export function SpacingLab() {
  const store = useStore();
  const font = store.state.font!;
  const [lines, setLines] = useState(DEFAULT_LINES);
  const [capPx, setCapPx] = useState(font.preview.capPx || 22);
  const [condense, setCondense] = useState(font.preview.condense || 0.895);
  const [thin, setThin] = useState(0);
  const [pitchOverride, setPitchOverride] = useState<number | "">("");
  const [refImg, setRefImg] = useState<string | null>(null);
  const [refOp, setRefOp] = useState(0.5);
  const [compiling, setCompiling] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const atlas = store.state.atlas;

  const numAtlas: Atlas = useMemo(() => {
    const a: Atlas = {};
    if (atlas)
      for (const [cp, g] of Object.entries(atlas)) a[Number(cp)] = g as AtlasGlyph;
    return a;
  }, [atlas]);

  const compile = async () => {
    setCompiling(true);
    setErr(null);
    try {
      const r = await api.compile();
      store.setAtlas(r.glyphs);
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setCompiling(false);
    }
  };

  const capH = useMemo(() => (atlas ? capHeight(numAtlas) : 0), [atlas, numAtlas]);
  const advR = useMemo(
    () => (capH ? advanceRatio(numAtlas, capH) : 0),
    [numAtlas, capH],
  );

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv || !atlas || !capH) return;
    const scaleUp = 3; // display magnification
    const gridLeft = 8;
    const rowGap = Math.round(capPx * 0.6);
    // compute canvas size
    let maxCols = 0;
    for (const ln of lines) maxCols = Math.max(maxCols, ln.length);
    const cw = pitchOverride !== "" ? Number(pitchOverride) : cellWidth(capPx, advR, condense);
    const rowH = capPx + rowGap;
    const W = Math.ceil(gridLeft * 2 + maxCols * cw);
    const H = Math.ceil(lines.length * rowH + rowGap);
    cv.width = W;
    cv.height = H;
    cv.style.width = `${W * scaleUp}px`;
    cv.style.height = `${H * scaleUp}px`;
    const ctx = cv.getContext("2d")!;
    ctx.imageSmoothingEnabled = false;
    ctx.fillStyle = "#f4f1ea";
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = "#141414";

    lines.forEach((ln, row) => {
      const baseline = Math.round(row * rowH + rowGap + capPx);
      let col = 0;
      for (const ch of ln) {
        if (ch === " ") {
          col += 1;
          continue;
        }
        const g = numAtlas[ch.codePointAt(0)!];
        if (!g) {
          col += 1;
          continue;
        }
        const bits = bitsForGlyph(g);
        const scale = capPx / capH;
        const s = scaledGlyph(g, capPx, capH, cw, condense);
        // draw-time height forces caps/digits/$ to capPx (draw_token_chars);
        // width stays the unforced scaled width.
        const h = renderHeight(ch, g, capPx, capH);
        const w = s.scaled_w;
        if (bits) {
          let bmp = nearestResize(bits, g.w, g.h, w, h);
          let fw = w;
          if (condense < 0.999) {
            fw = Math.max(1, pyRound(w * condense));
            bmp = nearestResize(bmp, w, h, fw, h);
          }
          const maxW = Math.max(1, pyRound(cw * 0.96));
          if (fw > maxW) {
            bmp = nearestResize(bmp, fw, h, maxW, h);
            fw = maxW;
          }
          if (thin > 0) bmp = thinInkMask(bmp, fw, h, thin, ch);
          const offPx = pyRound(g.off * scale);
          const x = Math.round(gridLeft + col * cw + (cw - fw) / 2);
          const yBottom = baseline + offPx;
          const yTop = yBottom - h;
          // draw
          const img = ctx.createImageData(fw, h);
          for (let yy = 0; yy < h; yy++)
            for (let xx = 0; xx < fw; xx++) {
              const on = bmp[yy * fw + xx];
              const idx = (yy * fw + xx) * 4;
              img.data[idx] = 20;
              img.data[idx + 1] = 20;
              img.data[idx + 2] = 20;
              img.data[idx + 3] = on ? 255 : 0;
            }
          ctx.putImageData(img, x, yTop);
        }
        col += 1;
      }
    });
  }, [lines, capPx, condense, thin, pitchOverride, atlas, capH, advR, numAtlas]);

  const cwNow = capH ? (pitchOverride !== "" ? Number(pitchOverride) : cellWidth(capPx, advR, condense)) : 0;

  return (
    <div className="spacing-tab">
      <div className="spacing-controls">
        <button className="mini" onClick={() => void compile()} disabled={compiling}>
          {compiling ? "Compiling…" : "Compile atlas"}
        </button>
        {!atlas && <span className="muted">compile first to populate the atlas</span>}
        {err && <span className="badge red">{err}</span>}
        <label className="ctl">
          <span>capPx <b>{capPx}</b></span>
          <input type="range" min={12} max={60} value={capPx} onChange={(e) => setCapPx(Number(e.target.value))} />
        </label>
        <label className="ctl">
          <span>condense <b>{condense.toFixed(3)}</b></span>
          <input type="range" min={0.7} max={1} step={0.005} value={condense} onChange={(e) => setCondense(Number(e.target.value))} />
        </label>
        <label className="ctl">
          <span>thin <b>{thin.toFixed(2)}</b></span>
          <input type="range" min={0} max={0.5} step={0.05} value={thin} onChange={(e) => setThin(Number(e.target.value))} />
        </label>
        <label className="ctl">
          <span>OCR pitch override</span>
          <input
            type="number"
            placeholder="auto"
            value={pitchOverride}
            onChange={(e) => setPitchOverride(e.target.value === "" ? "" : Number(e.target.value))}
          />
        </label>
      </div>

      <div className="spacing-meta">
        capH <b>{capH}</b> · advanceRatio <b>{advR.toFixed(4)}</b> · cellW <b>{cwNow.toFixed(2)}</b>
      </div>

      <div className="text-lines">
        {lines.map((ln, i) => (
          <input
            key={i}
            className="textline"
            value={ln}
            onChange={(e) => setLines((ls) => ls.map((x, j) => (j === i ? e.target.value : x)))}
          />
        ))}
        <button className="mini" onClick={() => setLines((ls) => [...ls, "NEW LINE"])}>+ line</button>
      </div>

      <div className="canvas-stage">
        {refImg && (
          <img src={refImg} className="ref-overlay" style={{ opacity: refOp }} alt="reference crop" />
        )}
        <canvas ref={canvasRef} className="spacing-canvas" />
      </div>

      <div className="spacing-controls">
        <label className="ctl">
          <span>A/B reference crop</span>
          <input
            type="file"
            accept="image/*"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) setRefImg(URL.createObjectURL(f));
            }}
          />
        </label>
        {refImg && (
          <label className="ctl">
            <span>ref opacity <b>{refOp.toFixed(2)}</b></span>
            <input type="range" min={0} max={1} step={0.05} value={refOp} onChange={(e) => setRefOp(Number(e.target.value))} />
          </label>
        )}
      </div>
    </div>
  );
}
