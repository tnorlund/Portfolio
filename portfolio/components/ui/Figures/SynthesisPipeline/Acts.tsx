import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  buildLabelBoxes,
  familiesIn,
  toCssRectInner,
} from "../AugmentationShowcase/labelGeometry";
import { ENTITY_DISPLAY_NAMES, LABEL_COLORS } from "../labelStyles";
import {
  cloudScale,
  glyphAnchors,
  glyphAnchorsCloud,
  glyphDotPoints,
  glyphDotPointsCloud,
  nodeCount,
  skeletonPathDs,
  skeletonPathDsCloud,
  skeletonViewBox,
} from "./geometry";
import {
  ActId,
  BOLD_WEIGHT_CALLOUT,
  CHAR_PRINT_COUNT,
  charCloudSrc,
  charPrintSrc,
  COMPOSE_GROUP_ORDER,
  DotParams,
  FONT_CODEPOINTS,
  fontGlyphSrc,
  finalSrc,
  Merchant,
  MERCHANTS,
  MERCHANT_LABELS,
  MerchantAssets,
  RECEIPT_DIMS,
  realSrc,
  realThumbSrc,
  REAL_THUMB_COUNT,
  WEIGHT_MAX,
  WEIGHT_MIN,
  WEIGHT_STEP,
} from "./pipelineData";
import styles from "./SynthesisPipeline.module.css";

export interface ActProps {
  merchant: Merchant;
  assets: MerchantAssets;
  /** Progress within this act, 0..1. */
  progress: number;
  active: boolean;
  reducedMotion: boolean;
  dotWeight: number;
  onWeightChange: (weight: number) => void;
}

const clamp01 = (n: number): number => Math.min(1, Math.max(0, n));

/** Map a sub-range of act progress to 0..1. */
const phase = (p: number, from: number, to: number): number =>
  clamp01((p - from) / (to - from));

const AssetPending: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => (
  <div className={styles.assetPending} data-testid="asset-pending">
    {children}
  </div>
);

/* ==================================================================== */
/* Act 1 — Raw material                                                  */
/* ==================================================================== */

const RawMaterialAct: React.FC<ActProps> = ({
  merchant,
  progress,
  reducedMotion,
}) => {
  const [failed, setFailed] = useState<Record<number, boolean>>({});
  const shownProgress = reducedMotion ? 1 : progress;
  const indices = Array.from({ length: REAL_THUMB_COUNT }, (_, i) => i);
  const mid = (REAL_THUMB_COUNT - 1) / 2;

  return (
    <div className={styles.thumbFan} data-testid="act-raw">
      {indices.map((i) => {
        const revealed = shownProgress > (i / REAL_THUMB_COUNT) * 0.8;
        const rotate = (i - mid) * 9;
        const lift = Math.abs(i - mid) * -6;
        const style: React.CSSProperties = {
          transform: `rotate(${rotate}deg) translateY(${lift}px)`,
          opacity: revealed ? 1 : 0,
        };
        if (failed[i]) {
          return (
            <div
              key={i}
              className={`${styles.thumb} ${styles.thumbMissing}`}
              style={style}
            >
              scan {i + 1}
            </div>
          );
        }
        return (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={i}
            src={realThumbSrc(merchant, i)}
            alt={`Real ${merchant} receipt scan ${i + 1}`}
            className={styles.thumb}
            style={style}
            loading="lazy"
            onError={() => setFailed((prev) => ({ ...prev, [i]: true }))}
          />
        );
      })}
    </div>
  );
};

/* ==================================================================== */
/* Act 2 — One character                                                 */
/* ==================================================================== */

const OneCharacterAct: React.FC<ActProps> = ({
  merchant,
  progress,
  reducedMotion,
}) => {
  const p = reducedMotion ? 1 : progress;
  // Stack phase indexes the flip-through; cloud fades in at the end.
  const stackP = phase(p, 0.2, 0.8);
  const activeIdx = Math.min(
    CHAR_PRINT_COUNT - 1,
    Math.floor(stackP * CHAR_PRINT_COUNT),
  );
  const cloudOpacity = reducedMotion ? 0.95 : phase(p, 0.75, 1);
  // Show the current print plus a few behind it, piling up.
  const layers = [activeIdx, activeIdx - 1, activeIdx - 2, activeIdx - 3].filter(
    (i) => i >= 0,
  );

  return (
    <div className={styles.charStack} data-testid="act-character">
      {layers.map((i, depth) => (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          key={i}
          src={charPrintSrc(merchant, i)}
          alt={depth === 0 ? `A real printed character` : ""}
          aria-hidden={depth !== 0}
          className={styles.charLayer}
          style={{
            opacity: (1 - cloudOpacity) * (1 - depth * 0.28),
            transform: `translate(${depth * 4}px, ${depth * -4}px) scale(${
              1 - depth * 0.02
            })`,
          }}
        />
      ))}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={charCloudSrc(merchant)}
        alt="Consensus letterform averaged from many prints"
        className={styles.charCloud}
        style={{ opacity: cloudOpacity }}
        data-testid="char-cloud"
      />
    </div>
  );
};

/* ==================================================================== */
/* Act 3 — The pen path                                                  */
/* ==================================================================== */

const PenPathAct: React.FC<ActProps> = ({
  merchant,
  assets,
  progress,
  reducedMotion,
}) => {
  const { skeleton, dotParams } = assets;
  const cloud = dotParams?.cloudGeom ?? null;
  const p = reducedMotion ? 1 : progress;

  // When cloudGeom is present, map the skeleton straight into the cloud PNG's
  // pixel box and draw both in the same viewBox so they can't drift. Without
  // it (e.g. a merchant whose cloudGeom hasn't been measured yet), fall back
  // to a self-fitted view box.
  const geom = useMemo(() => {
    if (!skeleton) {
      return null;
    }
    if (cloud) {
      const w = cloud.capHeightPx;
      return {
        paths: skeletonPathDsCloud(skeleton, cloud),
        anchors: glyphAnchorsCloud(skeleton, cloud),
        nodes: nodeCount(skeleton),
        viewBox: { minX: 0, minY: 0, width: cloud.imageW, height: cloud.imageH },
        aspect: `${cloud.imageW} / ${cloud.imageH}`,
        stroke: w * 0.05,
        anchorR: w * 0.05,
        handleW: w * 0.014,
        handleR: w * 0.03,
        preserve: "none" as const,
      };
    }
    const vb = skeletonViewBox(skeleton);
    return {
      paths: skeletonPathDs(skeleton),
      anchors: glyphAnchors(skeleton),
      nodes: nodeCount(skeleton),
      viewBox: vb,
      aspect: `${vb.width} / ${vb.height}`,
      stroke: 26,
      anchorR: 14,
      handleW: 5,
      handleR: 9,
      preserve: "xMidYMid meet" as const,
    };
  }, [skeleton, cloud]);

  if (!geom) {
    return (
      <AssetPending>
        The pen path draws from char_skeleton.json.
      </AssetPending>
    );
  }

  const draw = phase(p, 0, 0.7); // 0..1 path draw
  const anchorReveal = phase(p, 0.55, 1); // dots + handles fade in
  const { minX, minY, width, height } = geom.viewBox;

  return (
    <div
      className={styles.penStage}
      style={{ aspectRatio: geom.aspect }}
      data-testid="act-penpath"
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={charCloudSrc(merchant)}
        alt=""
        aria-hidden="true"
        className={styles.penCloudFill}
      />
      <svg
        className={styles.penSvg}
        viewBox={`${minX} ${minY} ${width} ${height}`}
        preserveAspectRatio={geom.preserve}
        aria-hidden="true"
      >
        {geom.paths.map((d, i) => (
          <path
            key={i}
            d={d}
            className={styles.penPath}
            pathLength={1}
            data-testid="pen-path"
            strokeWidth={geom.stroke}
            strokeDasharray={1}
            strokeDashoffset={1 - draw}
          />
        ))}
        <g style={{ opacity: anchorReveal }}>
          {geom.anchors.handles.map((h, i) => (
            <g key={`h-${i}`}>
              <line
                className={styles.handleLine}
                x1={h.from.x}
                y1={h.from.y}
                x2={h.to.x}
                y2={h.to.y}
                strokeWidth={geom.handleW}
              />
              <circle
                className={styles.handleDot}
                cx={h.to.x}
                cy={h.to.y}
                r={geom.handleR}
              />
            </g>
          ))}
          {geom.anchors.anchors.map((a, i) => (
            <circle
              key={`a-${i}`}
              className={styles.anchorDot}
              cx={a.x}
              cy={a.y}
              r={geom.anchorR}
              strokeWidth={geom.handleW}
              data-testid="anchor-dot"
            />
          ))}
        </g>
      </svg>
      <span className={styles.penBadge}>{geom.nodes} nodes</span>
    </div>
  );
};

/* ==================================================================== */
/* Act 4 — Thermal print + weight                                        */
/* ==================================================================== */

const THERMAL_STEP_UNITS = 55; // cap-unit arc-length between dot stamps

const ThermalAct: React.FC<ActProps> = ({
  merchant,
  assets,
  progress,
  active,
  reducedMotion,
  dotWeight,
  onWeightChange,
}) => {
  const { skeleton, dotParams } = assets;
  const cloud = dotParams?.cloudGeom ?? null;
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Same cap-unit -> cloud-pixel mapping as act 3, so the printed glyph is the
  // exact same shape and size. Falls back to a fitted box without cloudGeom.
  const dots = useMemo(() => {
    if (!skeleton) {
      return null;
    }
    if (cloud) {
      return {
        points: glyphDotPointsCloud(skeleton, cloud, THERMAL_STEP_UNITS),
        boxW: cloud.imageW,
        boxH: cloud.imageH,
        minX: 0,
        minY: 0,
        pxPerUnit: cloudScale(cloud),
      };
    }
    const vb = skeletonViewBox(skeleton);
    const pxPerUnit = 1; // fitted view box is already in cap units
    return {
      points: glyphDotPoints(skeleton, THERMAL_STEP_UNITS),
      boxW: vb.width,
      boxH: vb.height,
      minX: vb.minX,
      minY: vb.minY,
      pxPerUnit,
    };
  }, [skeleton, cloud]);

  const params: DotParams | null = dotParams;
  const p = reducedMotion ? 1 : progress;
  const aspect = dots ? `${dots.boxW} / ${dots.boxH}` : "1 / 1";

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !dots || !params) {
      return;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return; // jsdom / no 2d context
    }
    const dpr =
      typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
    // Render at the glyph box's own resolution; CSS scales it to fit.
    canvas.width = Math.round(dots.boxW * dpr);
    canvas.height = Math.round(dots.boxH * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, dots.boxW, dots.boxH);

    // dotSize is a diameter in cap units; radius scales with the live weight.
    const radius = (params.dotSize / 2) * dotWeight * dots.pxPerUnit;

    const reveal = active && !reducedMotion ? p : 1;
    const count = Math.max(1, Math.round(dots.points.length * reveal));

    ctx.fillStyle =
      getComputedStyle(canvas).getPropertyValue("--text-color").trim() ||
      "#222";
    for (let i = 0; i < count && i < dots.points.length; i += 1) {
      const pt = dots.points[i];
      ctx.beginPath();
      ctx.arc(
        pt.x - dots.minX,
        pt.y - dots.minY,
        Math.max(0.5, radius),
        0,
        Math.PI * 2,
      );
      ctx.fill();
    }
  }, [dots, params, dotWeight, p, active, reducedMotion]);

  const isBold = params ? Math.abs(dotWeight - params.weightBold) < 0.02 : false;

  return (
    <div className={styles.thermalWrap} data-testid="act-thermal">
      {dots && params ? (
        <>
          <canvas
            ref={canvasRef}
            className={styles.thermalCanvas}
            style={{ aspectRatio: aspect }}
            data-testid="thermal-canvas"
            aria-label={`Thermal dots for ${merchant} at weight ${dotWeight.toFixed(2)}`}
          />
          <div className={styles.weightControl}>
            <div className={styles.weightRow}>
              <span>Weight</span>
              <input
                type="range"
                className={styles.weightSlider}
                min={WEIGHT_MIN}
                max={WEIGHT_MAX}
                step={WEIGHT_STEP}
                value={dotWeight}
                onChange={(e) => onWeightChange(Number(e.target.value))}
                aria-label="Dot weight"
                data-testid="weight-slider"
              />
              <span className={styles.weightValue}>{dotWeight.toFixed(2)}</span>
            </div>
            <span className={styles.weightLabel} data-bold={isBold}>
              {isBold
                ? `Bold — ${BOLD_WEIGHT_CALLOUT[merchant]}`
                : "Bold isn't a second font. It's one parameter."}
            </span>
          </div>
        </>
      ) : (
        <AssetPending>
          Thermal dots stamp from char_skeleton.json + dot_params.json.
        </AssetPending>
      )}
    </div>
  );
};

/* ==================================================================== */
/* Act 5 — A whole font                                                  */
/* ==================================================================== */

const FONT_GRID_COLS = 12; // matches grid-template-columns on desktop
const HERO_FLIGHT_SCALE = 5.2; // how big the hero rides in from stage center

/** Smoothstep ease for the flight. */
const smooth = (t: number): number => t * t * (3 - 2 * t);

const WholeFontAct: React.FC<ActProps> = ({
  merchant,
  assets,
  progress,
  reducedMotion,
}) => {
  const p = reducedMotion ? 1 : progress;
  const total = FONT_CODEPOINTS.length;

  // The hero glyph handed forward from the thermal act flies into its OWN cell
  // in the atlas. Pick that cell (the skeleton's char if it's in the grid),
  // then reverse-FLIP: the cell rides in from stage center, big, and settles
  // to identity in its slot. Percent translates are relative to the cell box,
  // so this needs no DOM measurement and stays deterministic.
  const heroCp = useMemo(() => {
    const ch = assets.skeleton?.char;
    const cp = ch ? ch.codePointAt(0) : undefined;
    return cp && FONT_CODEPOINTS.includes(cp) ? cp : FONT_CODEPOINTS[0];
  }, [assets.skeleton]);
  const heroIdx = FONT_CODEPOINTS.indexOf(heroCp);
  const rows = Math.ceil(total / FONT_GRID_COLS);
  const heroCol = heroIdx % FONT_GRID_COLS;
  const heroRow = Math.floor(heroIdx / FONT_GRID_COLS);
  // Delta (in cell-box units) from the hero cell's center to the grid center.
  const dx = FONT_GRID_COLS / 2 - (heroCol + 0.5);
  const dy = rows / 2 - (heroRow + 0.5);

  const flight = smooth(phase(p, 0, 0.42)); // 0 = centered+big, 1 = in its cell

  return (
    <div className={styles.fontGrid} data-testid="act-font">
      {FONT_CODEPOINTS.map((cp, i) => {
        const isHero = cp === heroCp;
        // Non-hero cells cascade in after the hero starts landing; the hero is
        // always present because it is the object that traveled here.
        const shown = isHero || p >= 0.28 + (i / total) * 0.6;
        const heroStyle: React.CSSProperties | undefined = isHero
          ? {
              transform: `translate(${dx * (1 - flight) * 100}%, ${
                dy * (1 - flight) * 100
              }%) scale(${1 + (HERO_FLIGHT_SCALE - 1) * (1 - flight)})`,
              zIndex: flight < 1 ? 3 : undefined,
            }
          : undefined;
        const maskUrl = `url(${fontGlyphSrc(merchant, cp)})`;
        return (
          <div
            key={cp}
            className={`${styles.fontCell}${isHero ? ` ${styles.fontCellHero}` : ""}`}
            data-shown={shown}
            data-hero={isHero || undefined}
            data-testid="font-cell"
            style={heroStyle}
          >
            {/* Type on the page: the alpha-mask glyph inherits the page text
                color (currentColor) on the transparent page background, in both
                themes. */}
            <div
              className={styles.fontGlyph}
              style={{ WebkitMaskImage: maskUrl, maskImage: maskUrl }}
              aria-hidden="true"
            />
          </div>
        );
      })}
    </div>
  );
};

/* ==================================================================== */
/* Act 6 — The receipt assembles (typing + LayoutLM boxes)               */
/* ==================================================================== */

const TYPE_START = 0.04;
const TYPE_END = 0.5;

/**
 * The former style / compose / print+labels acts merged into one. Phase A: the
 * receipt types itself out, header/items/summary/footer all at once — four
 * concurrent print heads revealing their words in order from final.webp. Phase
 * B: the ground-truth label boxes draw on, styled exactly like the LayoutLM
 * inference viz (LABEL_COLORS, fillOpacity 0.3, stroke 2, no vectorEffect).
 */
const AssembleAct: React.FC<ActProps> = ({
  merchant,
  assets,
  progress,
  reducedMotion,
}) => {
  const { compose, finalLabels } = assets;
  const p = reducedMotion ? 1 : progress;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [imgReady, setImgReady] = useState(false);

  const render = finalLabels?.metadata?.render;
  const renderW = render?.width ?? 760;
  const renderH = render?.height ?? 2471;
  const aspect = `${renderW} / ${renderH}`;

  // Every word's rect (labeled OR not), for the typing reveal.
  const wordRects = useMemo(() => {
    if (!finalLabels || !render) {
      return [] as Array<ReturnType<typeof toCssRectInner> | null>;
    }
    return finalLabels.bboxes.map((bb) =>
      bb && bb.length === 4 ? toCssRectInner(bb, render) : null,
    );
  }, [finalLabels, render]);

  // The four section groups (contiguous word-index ranges), typed in parallel.
  const groups = useMemo(() => {
    if (!compose) {
      return [] as number[][];
    }
    return COMPOSE_GROUP_ORDER.map((id) => compose.groups[id] ?? []).filter(
      (g) => g.length > 0,
    );
  }, [compose]);

  // Labeled boxes + families for phase B (LayoutLM-styled).
  const boxes = useMemo(
    () => (finalLabels ? buildLabelBoxes(finalLabels) : []),
    [finalLabels],
  );
  const families = useMemo(
    () => (finalLabels ? familiesIn(finalLabels) : []),
    [finalLabels],
  );

  const typingP = phase(p, TYPE_START, TYPE_END);
  const labelsShown = p >= TYPE_END + 0.06;

  // Load the printed receipt once.
  useEffect(() => {
    if (!finalLabels) {
      return;
    }
    const img = new window.Image();
    img.onload = () => {
      imgRef.current = img;
      setImgReady(true);
    };
    img.src = finalSrc(merchant);
    return () => {
      img.onload = null;
    };
  }, [merchant, finalLabels]);

  // Draw the revealed words. Each group reveals in order, all groups at once, so
  // the receipt materializes top-to-bottom in four places simultaneously; a
  // caret blinks at each section's leading edge.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return; // jsdom / no 2d context
    }
    canvas.width = renderW;
    canvas.height = renderH;
    ctx.clearRect(0, 0, renderW, renderH);
    const img = imgRef.current;
    if (!img || !imgReady) {
      return;
    }
    const t = clamp01(typingP);
    const drawWordCrop = (r: ReturnType<typeof toCssRectInner>) => {
      const sx = (r.left / 100) * renderW;
      const sy = (r.top / 100) * renderH;
      const sw = (r.width / 100) * renderW;
      const sh = (r.height / 100) * renderH;
      if (sw > 0 && sh > 0) {
        ctx.drawImage(img, sx, sy, sw, sh, sx, sy, sw, sh);
      }
    };
    groups.forEach((g) => {
      const revealed = Math.round(t * g.length);
      for (let i = 0; i < revealed; i += 1) {
        const r = wordRects[g[i]];
        if (r) {
          drawWordCrop(r);
        }
      }
    });
    // Blinking carets at each leading edge while typing.
    if (t > 0 && t < 1 && Math.floor(t * 48) % 2 === 0) {
      ctx.fillStyle =
        getComputedStyle(canvas).getPropertyValue("--color-blue").trim() ||
        "#4a90d9";
      groups.forEach((g) => {
        const revealed = Math.round(t * g.length);
        const r = wordRects[g[Math.min(g.length - 1, revealed)]];
        if (r) {
          ctx.fillRect(
            (r.left / 100) * renderW - 4,
            (r.top / 100) * renderH,
            6,
            (r.height / 100) * renderH,
          );
        }
      });
    }
  }, [typingP, imgReady, groups, wordRects, renderW, renderH]);

  if (!finalLabels || !compose) {
    return (
      <AssetPending>
        The receipt assembles from compose_steps.json + final.labels.json.
      </AssetPending>
    );
  }

  return (
    <div className={styles.assembleStage} data-testid="act-assemble">
      <div className={styles.assembleReceipt} style={{ aspectRatio: aspect }}>
        <canvas
          ref={canvasRef}
          className={styles.assembleCanvas}
          data-testid="assemble-canvas"
          aria-label={`Synthetic ${merchant} receipt assembling`}
        />
        {labelsShown ? (
          <svg
            className={styles.assembleBoxes}
            viewBox={`0 0 ${renderW} ${renderH}`}
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            {boxes.map((box, order) => {
              const color = LABEL_COLORS[box.family] || LABEL_COLORS.O;
              return (
                <rect
                  key={box.index}
                  data-testid="final-label-box"
                  data-family={box.family}
                  className={styles.labelBox}
                  style={{ animationDelay: `${order * 20}ms` }}
                  x={(box.rect.left / 100) * renderW}
                  y={(box.rect.top / 100) * renderH}
                  width={(box.rect.width / 100) * renderW}
                  height={(box.rect.height / 100) * renderH}
                  fill={color}
                  fillOpacity={0.3}
                  stroke={color}
                  strokeWidth={2}
                />
              );
            })}
          </svg>
        ) : null}
      </div>
      {labelsShown ? (
        <div className={styles.assembleSide}>
          <ul className={styles.legend} aria-label="Label families">
            {families.map((family) => (
              <li key={family} className={styles.legendItem}>
                <span
                  className={styles.legendSwatch}
                  style={{
                    backgroundColor: LABEL_COLORS[family] || LABEL_COLORS.O,
                  }}
                />
                {ENTITY_DISPLAY_NAMES[family] ?? family}
              </li>
            ))}
          </ul>
          <p className={styles.counter} data-testid="labels-counter">
            Labeled training example. Zero manual labels.
          </p>
        </div>
      ) : null}
    </div>
  );
};

/* (Acts 7 & 8 — Compose and Print+labels — merged into AssembleAct above.) */

/* ==================================================================== */
/* Act 9 — Finale: same machine, every store                            */
/* ==================================================================== */

/**
 * One merchant's finale card: a before/after PAIR. The real scan and our
 * synthesized render (both normalized to the same 760-wide box, so they align
 * 1:1) are stacked, and a divider auto-wipes left-right — as it sweeps, the
 * receipt stays continuous line-for-line, which is the proof that the synth
 * matches the real. The card renders at a common width and its own natural
 * height (Costco taller than Vons taller than Sprouts).
 */
const FinaleCard: React.FC<{
  merchant: Merchant;
  shown: boolean;
  wipe: number;
}> = ({ merchant, shown, wipe }) => {
  const [synthFailed, setSynthFailed] = useState(false);
  const [realFailed, setRealFailed] = useState(false);
  const dims = RECEIPT_DIMS[merchant];
  const wipePct = Math.round(clamp01(wipe) * 1000) / 10;
  const pair = !synthFailed && !realFailed;

  return (
    <figure
      className={styles.finaleCard}
      data-shown={shown}
      data-testid="finale-card"
      data-merchant={merchant}
    >
      <div
        className={styles.finaleFrame}
        style={{ aspectRatio: `${dims.w} / ${dims.h}` }}
      >
        {!synthFailed ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={finalSrc(merchant)}
            alt={`Synthetic ${merchant} receipt`}
            className={styles.finaleSynth}
            loading="lazy"
            onError={() => setSynthFailed(true)}
            data-testid="finale-image"
          />
        ) : (
          <div className={styles.finaleFallback} data-testid="finale-fallback">
            {MERCHANT_LABELS[merchant]} receipt
          </div>
        )}
        {pair ? (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={realSrc(merchant)}
              alt={`Real ${merchant} receipt scan`}
              className={styles.finaleReal}
              loading="lazy"
              onError={() => setRealFailed(true)}
              style={{ clipPath: `inset(0 ${100 - wipePct}% 0 0)` }}
              data-testid="finale-real"
            />
            <span
              className={styles.finaleDivider}
              style={{ left: `${wipePct}%` }}
              aria-hidden="true"
            />
            <span className={`${styles.finaleChip} ${styles.finaleChipReal}`}>
              real
            </span>
            <span className={`${styles.finaleChip} ${styles.finaleChipSynth}`}>
              synth
            </span>
          </>
        ) : null}
      </div>
      <figcaption className={styles.finaleName}>
        {MERCHANT_LABELS[merchant]}
      </figcaption>
    </figure>
  );
};

const FinaleAct: React.FC<ActProps> = ({ progress, reducedMotion }) => {
  const p = reducedMotion ? 1 : progress;
  // Divider oscillates across the pair; rests centered (0.5) at p=0/1 so a
  // paused finale shows a clean real|synth split.
  const wipe = 0.5 + 0.42 * Math.sin(p * Math.PI * 2);
  return (
    <div className={styles.finaleRow} data-testid="act-finale">
      {MERCHANTS.map((m, i) => (
        <FinaleCard
          key={m}
          merchant={m}
          shown={p >= (i / MERCHANTS.length) * 0.7}
          wipe={wipe}
        />
      ))}
    </div>
  );
};

/* ==================================================================== */
/* Dispatcher                                                            */
/* ==================================================================== */

const ACT_COMPONENTS: Record<ActId, React.FC<ActProps>> = {
  raw: RawMaterialAct,
  character: OneCharacterAct,
  penpath: PenPathAct,
  thermal: ThermalAct,
  font: WholeFontAct,
  assemble: AssembleAct,
  finale: FinaleAct,
};

export const ActView: React.FC<{ actId: ActId } & ActProps> = ({
  actId,
  ...props
}) => {
  const Component = ACT_COMPONENTS[actId];
  return <Component {...props} />;
};
