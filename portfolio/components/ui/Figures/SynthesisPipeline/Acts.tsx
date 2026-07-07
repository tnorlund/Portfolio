import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  buildLabelBoxes,
  familiesIn,
  toCssRectInner,
} from "../AugmentationShowcase/labelGeometry";
import { LABEL_COLORS } from "../labelStyles";
import { LabelBoxOverlay, LabelLegend } from "../labelBoxOverlay";
import sharedStyles from "../labelBoxOverlay.module.css";
import {
  cloudScale,
  glyphAnchorsCloud,
  glyphDotPointsCloud,
  nodeCount,
  skeletonPathDsCloud,
} from "./geometry";
import {
  ActId,
  BOLD_WEIGHT_CALLOUT,
  CHAR_PRINT_COUNT,
  charCloudSrc,
  charPrintSrc,
  COMPOSE_GROUP_ORDER,
  FONT_CODEPOINTS,
  fontGlyphSrc,
  finalSrc,
  logoSrc,
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
/* Act 2 — One character: prints -> trace (with handles) -> thermal dots  */
/* ==================================================================== */

const THERMAL_STEP_UNITS = 55; // cap-unit arc-length between dot stamps
const GLYPH_DISPLAY_PX = 290; // approx on-screen height of the glyph box
const CHAR_STACK_LAYERS = 4;

/**
 * The former character / pen-path / thermal acts merged into one continuous
 * beat, all in the SAME cloudGeom frame: real prints stack into the consensus
 * cloud, the pen path draws over it WITH vector-editor handles (filled square
 * anchors, hairline handles to hollow control circles — mapped through the
 * identical cloud transform as the path), then the handles fade as the thermal
 * dots stamp along the path. The weight slider stays live throughout.
 */
const CharacterAct: React.FC<ActProps> = ({
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
  const p = reducedMotion ? 1 : progress;

  const geom = useMemo(() => {
    if (!skeleton || !cloud) {
      return null;
    }
    return {
      paths: skeletonPathDsCloud(skeleton, cloud),
      anchors: glyphAnchorsCloud(skeleton, cloud),
      points: glyphDotPointsCloud(skeleton, cloud, THERMAL_STEP_UNITS),
      nodes: nodeCount(skeleton),
      viewBox: { width: cloud.imageW, height: cloud.imageH },
      aspect: `${cloud.imageW} / ${cloud.imageH}`,
      pxPerUnit: cloudScale(cloud),
      stroke: cloud.capHeightPx * 0.045,
    };
  }, [skeleton, cloud]);

  // ---- Beat phases ----
  const cloudOpacity = reducedMotion ? 0.4 : phase(p, 0.1, 0.28);
  const printsOpacity = reducedMotion ? 0 : 1 - phase(p, 0.04, 0.24);
  const draw = reducedMotion ? 0 : phase(p, 0.28, 0.6); // 0..1 path draw
  const handlesOpacity = reducedMotion
    ? 0
    : phase(p, 0.42, 0.54) * (1 - phase(p, 0.62, 0.74));
  const dotReveal = reducedMotion ? 1 : phase(p, 0.62, 1);

  // Thermal dots stamp along the path as the handles fade.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !geom || !dotParams) {
      return;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return; // jsdom / no 2d context
    }
    const dpr =
      typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
    canvas.width = Math.round(geom.viewBox.width * dpr);
    canvas.height = Math.round(geom.viewBox.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, geom.viewBox.width, geom.viewBox.height);
    const radius = (dotParams.dotSize / 2) * dotWeight * geom.pxPerUnit;
    const reveal = active && !reducedMotion ? dotReveal : 1;
    const count = Math.max(0, Math.round(geom.points.length * reveal));
    ctx.fillStyle =
      getComputedStyle(canvas).getPropertyValue("--text-color").trim() ||
      "#222";
    for (let i = 0; i < count && i < geom.points.length; i += 1) {
      const pt = geom.points[i];
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, Math.max(0.5, radius), 0, Math.PI * 2);
      ctx.fill();
    }
  }, [geom, dotParams, dotWeight, dotReveal, active, reducedMotion]);

  if (!geom || !dotParams) {
    return (
      <AssetPending>
        The character is mined from char_skeleton.json + dot_params.json.
      </AssetPending>
    );
  }

  const { width, height } = geom.viewBox;
  // Vector-editor handle geometry, sized in screen px (converted to viewBox
  // units so anchors/handles read at a constant on-screen size).
  const s = height / GLYPH_DISPLAY_PX;
  const anchorHalf = 1.75 * s; // ~3.5px filled square
  const handleR = 2.5 * s; // ~2.5px hollow circle
  const isBold = Math.abs(dotWeight - dotParams.weightBold) < 0.02;
  const stackP = phase(p, 0.02, 0.24);
  const activeIdx = Math.min(
    CHAR_PRINT_COUNT - 1,
    Math.floor(stackP * CHAR_PRINT_COUNT),
  );
  const printLayers = Array.from({ length: CHAR_STACK_LAYERS }, (_, d) => activeIdx - d).filter(
    (i) => i >= 0,
  );

  return (
    <div className={styles.charStageWrap} data-testid="act-character">
      <div className={styles.charGlyphBox} style={{ aspectRatio: geom.aspect }}>
        {/* Consensus cloud (the shared frame everything maps into). */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={charCloudSrc(merchant)}
          alt="Consensus letterform averaged from many prints"
          className={styles.charCloudFill}
          style={{ opacity: cloudOpacity }}
          data-testid="char-cloud"
        />
        {/* Real prints piling up, fading as the cloud resolves. */}
        {printsOpacity > 0.01
          ? printLayers.map((i, depth) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={i}
                src={charPrintSrc(merchant, i)}
                alt=""
                aria-hidden="true"
                className={styles.charPrintLayer}
                style={{
                  opacity: printsOpacity * (1 - depth * 0.28),
                  transform: `translate(${depth * 4}px, ${depth * -4}px)`,
                }}
              />
            ))
          : null}
        {/* Pen path + vector-editor handles, in the cloud's pixel space. */}
        <svg
          className={styles.penSvg}
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
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
          <g style={{ opacity: handlesOpacity }}>
            {geom.anchors.handles.map((h, i) => (
              <g key={`h-${i}`}>
                <line
                  className={styles.handleLine}
                  x1={h.from.x}
                  y1={h.from.y}
                  x2={h.to.x}
                  y2={h.to.y}
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
                <circle
                  className={styles.handleDot}
                  cx={h.to.x}
                  cy={h.to.y}
                  r={handleR}
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
              </g>
            ))}
            {geom.anchors.anchors.map((a, i) => (
              <rect
                key={`a-${i}`}
                className={styles.anchorSquare}
                data-testid="anchor-dot"
                x={a.x - anchorHalf}
                y={a.y - anchorHalf}
                width={anchorHalf * 2}
                height={anchorHalf * 2}
              />
            ))}
          </g>
        </svg>
        {/* Thermal dots stamped along the path. */}
        <canvas
          ref={canvasRef}
          className={styles.charThermalCanvas}
          data-testid="thermal-canvas"
          aria-label={`Thermal dots for ${merchant} at weight ${dotWeight.toFixed(2)}`}
        />
        <span className={styles.penBadge}>{geom.nodes} nodes</span>
      </div>
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

  // Boxes reveal progressively (LayoutLM-style slicing — they simply appear,
  // top to bottom, no transform animation), mapped into receipt pixel space.
  const revealCount = Math.round(
    clamp01(phase(p, TYPE_END + 0.06, 0.9)) * boxes.length,
  );
  const overlayBoxes = boxes.slice(0, revealCount).map((box) => ({
    key: box.index,
    x: (box.rect.left / 100) * renderW,
    y: (box.rect.top / 100) * renderH,
    width: (box.rect.width / 100) * renderW,
    height: (box.rect.height / 100) * renderH,
    color: LABEL_COLORS[box.family] || LABEL_COLORS.O,
    testId: "final-label-box",
    family: box.family,
  }));

  return (
    <div className={styles.assembleStage} data-testid="act-assemble">
      <div
        className={`${styles.assembleReceipt} ${sharedStyles.receiptCard}`}
        style={{ aspectRatio: aspect }}
      >
        <canvas
          ref={canvasRef}
          className={styles.assembleCanvas}
          data-testid="assemble-canvas"
          aria-label={`Synthetic ${merchant} receipt assembling`}
        />
        {labelsShown ? (
          <LabelBoxOverlay
            className={styles.assembleBoxes}
            width={renderW}
            height={renderH}
            boxes={overlayBoxes}
          />
        ) : null}
      </div>
      {labelsShown ? (
        <div className={styles.assembleSide}>
          <LabelLegend families={families} />
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
  const logoUrl = `url(${logoSrc(merchant)})`;
  const wipePct = Math.round(clamp01(wipe) * 1000) / 10;
  const pair = !synthFailed && !realFailed;

  return (
    <figure
      className={styles.finaleCard}
      data-shown={shown}
      data-testid="finale-card"
      data-merchant={merchant}
    >
      {/* Merchant logo mark: currentColor through an alpha mask (theme-aware),
          above the pair — replaces the text caption. A fixed box + mask
          contain fits every aspect (Trader Joe's/CVS are long wordmarks). */}
      <div
        className={styles.finaleLogo}
        role="img"
        aria-label={`${MERCHANT_LABELS[merchant]} logo`}
        style={{
          WebkitMaskImage: logoUrl,
          maskImage: logoUrl,
        }}
      />
      <div
        className={styles.finaleFrame}
        data-testid="finale-frame"
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
            {/* Mobile: one indicator that names whichever half currently
                dominates the wipe (shown via CSS on narrow cards). */}
            <span className={styles.finaleChipMobile}>
              {wipePct >= 50 ? "real" : "synth"}
            </span>
          </>
        ) : null}
      </div>
    </figure>
  );
};

const FinaleAct: React.FC<ActProps> = ({ progress, reducedMotion }) => {
  const p = reducedMotion ? 1 : progress;
  // Divider oscillates across the pair; rests centered (0.5) at p=0/1 so a
  // paused finale shows a clean real|synth split.
  const wipe = 0.5 + 0.42 * Math.sin(p * Math.PI * 2);
  const rowRef = useRef<HTMLDivElement>(null);

  // Let a vertical mouse wheel scroll the pair row horizontally (trackpad and
  // touch already scroll it natively). Only intercept when the row can scroll
  // further in that direction, so the page still scrolls at the ends.
  useEffect(() => {
    const el = rowRef.current;
    if (!el) {
      return;
    }
    const onWheel = (e: WheelEvent) => {
      const max = el.scrollWidth - el.clientWidth;
      if (max <= 1 || e.deltaY === 0 || Math.abs(e.deltaY) < Math.abs(e.deltaX)) {
        return; // nothing to scroll, or already a horizontal gesture
      }
      const atStart = el.scrollLeft <= 0;
      const atEnd = el.scrollLeft >= max - 1;
      if ((e.deltaY < 0 && atStart) || (e.deltaY > 0 && atEnd)) {
        return; // let the page take over at the ends
      }
      e.preventDefault();
      el.scrollLeft += e.deltaY;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  return (
    <div ref={rowRef} className={styles.finaleRow} data-testid="act-finale">
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
  character: CharacterAct,
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
