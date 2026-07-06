import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  buildLabelBoxes,
  familiesIn,
  familyColors,
  ShowcaseLabelFile,
} from "../AugmentationShowcase/labelGeometry";
import { ENTITY_DISPLAY_NAMES } from "../labelStyles";
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
  assetRoot,
  BOLD_WEIGHT_CALLOUT,
  CHAR_PRINT_COUNT,
  charCloudSrc,
  charPrintSrc,
  COMPOSE_GROUP_LABELS,
  COMPOSE_GROUP_ORDER,
  DotParams,
  FONT_CODEPOINTS,
  fontGlyphSrc,
  finalSrc,
  Merchant,
  MERCHANTS,
  MERCHANT_LABELS,
  MerchantAssets,
  realThumbSrc,
  REAL_THUMB_COUNT,
  styleCropSrc,
  StyleSection,
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

const WholeFontAct: React.FC<ActProps> = ({
  merchant,
  progress,
  reducedMotion,
}) => {
  const [failed, setFailed] = useState<Record<number, boolean>>({});
  const p = reducedMotion ? 1 : progress;
  const total = FONT_CODEPOINTS.length;

  return (
    <div className={styles.fontGrid} data-testid="act-font">
      {FONT_CODEPOINTS.map((cp, i) => {
        const shown = p >= (i / total) * 0.9;
        return (
          <div
            key={cp}
            className={styles.fontCell}
            data-shown={shown}
            data-testid="font-cell"
          >
            {!failed[cp] ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={fontGlyphSrc(merchant, cp)}
                alt=""
                aria-hidden="true"
                className={styles.fontGlyph}
                loading="lazy"
                onError={() =>
                  setFailed((prev) => ({ ...prev, [cp]: true }))
                }
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
};

/* ==================================================================== */
/* Act 6 — Measured style                                                */
/* ==================================================================== */

const FALLBACK_STYLE: Record<Merchant, StyleSection[]> = {
  sprouts: [
    { name: "section_header", display: "Category headers underlined" },
    { name: "balance_due", display: "BALANCE DUE bold and taller" },
    { name: "payment", display: "Payment lines condensed" },
  ],
  costco: [
    { name: "total_line", display: "TOTAL knocked out (reverse video)" },
    { name: "self_checkout", display: "Display headings heavy and enlarged" },
    { name: "date_box", display: "Date printed in a box" },
  ],
  vons: [
    { name: "club_savings", display: "Club savings called out per line" },
    { name: "total_line", display: "TOTAL bold and taller" },
    { name: "coupon", display: "Coupons printed below the total" },
  ],
};

/** `section_header` -> `Section header`. */
const prettyName = (name: string): string => {
  const spaced = name.replace(/_/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
};

/** Resolve a section's crop path (relative to the merchant asset root). */
const styleCropFor = (merchant: Merchant, section: StyleSection): string =>
  section.crop
    ? `${assetRoot(merchant)}/${section.crop}`
    : styleCropSrc(merchant, section.name);

const MeasuredStyleAct: React.FC<ActProps> = ({
  merchant,
  assets,
  progress,
  reducedMotion,
}) => {
  const [failed, setFailed] = useState<Record<string, boolean>>({});
  const sections =
    assets.style?.sections && assets.style.sections.length > 0
      ? assets.style.sections
      : FALLBACK_STYLE[merchant];
  const p = reducedMotion ? 1 : progress;

  return (
    <div className={styles.styleList} data-testid="act-style">
      {sections.map((section, i) => {
        const lit = p >= (i / sections.length) * 0.85;
        return (
          <div
            key={section.name}
            className={styles.styleRow}
            data-lit={lit}
            data-testid="style-row"
          >
            {!failed[section.name] ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={styleCropFor(merchant, section)}
                alt=""
                aria-hidden="true"
                className={styles.styleCrop}
                loading="lazy"
                onError={() =>
                  setFailed((prev) => ({ ...prev, [section.name]: true }))
                }
              />
            ) : (
              <span className={styles.styleCrop} aria-hidden="true" />
            )}
            <span className={styles.styleText}>
              <span className={styles.styleLabel}>{prettyName(section.name)}</span>
              <span className={styles.styleValue}>{section.display}</span>
            </span>
            <span className={styles.styleMeasured}>measured</span>
          </div>
        );
      })}
    </div>
  );
};

/* ==================================================================== */
/* Act 7 — Compose                                                       */
/* ==================================================================== */

const COMPOSE_TOKEN_CAP = 16;

const ComposeAct: React.FC<ActProps> = ({
  assets,
  progress,
  reducedMotion,
}) => {
  const { compose, finalLabels } = assets;
  const p = reducedMotion ? 1 : progress;

  // Each group is a list of token indices into final.labels.json; join them
  // into a readable block (capped so the footer's long tail stays legible).
  const groups = useMemo(() => {
    if (!compose || !finalLabels) {
      return null;
    }
    return COMPOSE_GROUP_ORDER.map((id) => {
      const indices = compose.groups[id] ?? [];
      const tokens = indices
        .map((idx) => finalLabels.tokens[idx])
        .filter((t): t is string => Boolean(t));
      const text =
        tokens.slice(0, COMPOSE_TOKEN_CAP).join(" ") +
        (tokens.length > COMPOSE_TOKEN_CAP ? " …" : "");
      return { id, label: COMPOSE_GROUP_LABELS[id], text, count: tokens.length };
    }).filter((group) => group.count > 0);
  }, [compose, finalLabels]);

  if (!groups) {
    return (
      <AssetPending>
        Composed content reveals from compose_steps.json + final.labels.json.
      </AssetPending>
    );
  }

  const grandTotal = (
    finalLabels?.metadata as { quality?: { grand_total?: string } } | undefined
  )?.quality?.grand_total;

  return (
    <div className={styles.composeSheet} data-testid="act-compose">
      {groups.map((group, i) => {
        const shown = p >= (i / groups.length) * 0.85;
        return (
          <div
            key={group.id}
            className={styles.composeGroup}
            data-group={group.id}
            data-shown={shown}
            data-testid="compose-group"
          >
            <span className={styles.composeGroupLabel}>{group.label}</span>
            <span className={styles.composeGroupText}>{group.text}</span>
          </div>
        );
      })}
      {grandTotal ? (
        <div
          className={styles.composeTotal}
          data-shown={p > 0.9}
          data-testid="compose-total"
        >
          <span>TOTAL</span>
          <span>${grandTotal}</span>
        </div>
      ) : null}
    </div>
  );
};

/* ==================================================================== */
/* Act 8 — Print + labels                                                */
/* ==================================================================== */

interface FinalLegendProps {
  labels: ShowcaseLabelFile;
}

const FinalLegend: React.FC<FinalLegendProps> = ({ labels }) => {
  const families = useMemo(() => familiesIn(labels), [labels]);
  const colors = useMemo(() => familyColors(families), [families]);
  return (
    <ul className={styles.legend} aria-label="Label families">
      {families.map((family) => (
        <li key={family} className={styles.legendItem}>
          <span
            className={styles.legendSwatch}
            style={{ backgroundColor: colors[family] }}
          />
          {ENTITY_DISPLAY_NAMES[family] ?? family}
        </li>
      ))}
    </ul>
  );
};

const PrintLabelsAct: React.FC<ActProps> = ({
  merchant,
  assets,
  progress,
  reducedMotion,
}) => {
  const [imgFailed, setImgFailed] = useState(false);
  const labels = assets.finalLabels;
  const p = reducedMotion ? 1 : progress;

  const boxes = useMemo(
    () => (labels ? buildLabelBoxes(labels) : []),
    [labels],
  );
  const colors = useMemo(
    () => (labels ? familyColors(familiesIn(labels)) : {}),
    [labels],
  );

  const printP = phase(p, 0, 0.65);
  const labelsShown = p >= 0.7;
  const aspect =
    labels?.metadata?.render &&
    labels.metadata.render.width &&
    labels.metadata.render.height
      ? `${labels.metadata.render.width} / ${labels.metadata.render.height}`
      : "2 / 3";

  return (
    <div className={styles.printStage} data-testid="act-labels">
      {!imgFailed ? (
        <div className={styles.printFrame} style={{ aspectRatio: aspect }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={finalSrc(merchant)}
            alt={`Synthetic ${merchant} receipt, printed`}
            className={styles.printImage}
            style={{ clipPath: `inset(0 0 ${(1 - printP) * 100}% 0)` }}
            onError={() => setImgFailed(true)}
          />
          {printP < 1 && printP > 0 ? (
            <div
              className={styles.printHead}
              style={{ top: `${printP * 100}%` }}
              aria-hidden="true"
            />
          ) : null}
          {labels && labelsShown ? (
            <svg
              className={styles.overlay}
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              {boxes.map((box, order) => (
                <rect
                  key={box.index}
                  data-testid="final-label-box"
                  data-family={box.family}
                  className={styles.labelBox}
                  style={{ animationDelay: `${order * 25}ms` }}
                  x={box.rect.left}
                  y={box.rect.top}
                  width={box.rect.width}
                  height={box.rect.height}
                  fill={colors[box.family]}
                  fillOpacity={0.3}
                  stroke={colors[box.family]}
                  strokeWidth={2}
                  vectorEffect="non-scaling-stroke"
                >
                  <title>{`${box.token} — ${box.family}`}</title>
                </rect>
              ))}
            </svg>
          ) : null}
        </div>
      ) : (
        <div className={styles.printFallback} data-testid="print-fallback">
          The printed receipt lands as final.webp.
        </div>
      )}
      <div>
        {labels && labelsShown ? <FinalLegend labels={labels} /> : null}
        {labels && labelsShown ? (
          <p className={styles.counter} data-testid="labels-counter">
            Labeled training example. Zero manual labels.
          </p>
        ) : null}
      </div>
    </div>
  );
};

/* ==================================================================== */
/* Act 9 — Finale: same machine, every store                            */
/* ==================================================================== */

const FinaleCard: React.FC<{ merchant: Merchant; shown: boolean }> = ({
  merchant,
  shown,
}) => {
  const [failed, setFailed] = useState(false);
  return (
    <figure
      className={styles.finaleCard}
      data-shown={shown}
      data-testid="finale-card"
      data-merchant={merchant}
    >
      <div className={styles.finaleFrame}>
        {!failed ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={finalSrc(merchant)}
            alt={`Synthetic ${merchant} receipt`}
            className={styles.finaleImage}
            loading="lazy"
            onError={() => setFailed(true)}
            data-testid="finale-image"
          />
        ) : (
          <div className={styles.finaleFallback} data-testid="finale-fallback">
            {MERCHANT_LABELS[merchant]} receipt
          </div>
        )}
      </div>
      <figcaption className={styles.finaleName}>
        {MERCHANT_LABELS[merchant]}
      </figcaption>
    </figure>
  );
};

const FinaleAct: React.FC<ActProps> = ({ progress, reducedMotion }) => {
  const p = reducedMotion ? 1 : progress;
  return (
    <div className={styles.finaleRow} data-testid="act-finale">
      {MERCHANTS.map((m, i) => (
        <FinaleCard
          key={m}
          merchant={m}
          shown={p >= (i / MERCHANTS.length) * 0.75}
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
  style: MeasuredStyleAct,
  compose: ComposeAct,
  labels: PrintLabelsAct,
  finale: FinaleAct,
};

export const ActView: React.FC<{ actId: ActId } & ActProps> = ({
  actId,
  ...props
}) => {
  const Component = ACT_COMPONENTS[actId];
  return <Component {...props} />;
};
