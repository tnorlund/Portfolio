import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import {
  PORTRAIT_PROCESSING_SIZE,
  PORTRAIT_ROTOSCOPE_OPTIONS,
} from "../home/Rotoscope/portraitConfig";
import type { FocusTierName } from "../home/Rotoscope/algorithm";
import styles from "../../styles/Rotoscope.module.css";
import {
  clampInterior,
  localMaxAt,
  markerPixelAt,
  prepareMarkerFields,
  shiTomasiAt,
  type MarkerFields,
} from "./markerMath";
import type { RgbaBuffer } from "./pixelMath";

const STEPS = [
  { id: "score", label: "Score" },
  { id: "maxima", label: "Local max" },
  { id: "tiers", label: "Tiers" },
] as const;

type StepId = (typeof STEPS)[number]["id"];

const SAMPLE_POINTS = [
  { x: 0.367, y: 0.518, label: "left eye" },
  { x: 0.456, y: 0.525, label: "right eye" },
  { x: 0.41, y: 0.62, label: "mouth" },
  { x: 0.33, y: 0.44, label: "hair" },
  { x: 0.78, y: 0.3, label: "background" },
] as const;

const PIXEL_DWELL_MS = 1600;
const IDLE_RESUME_MS = 10000;
const BLUR_RADIUS = PORTRAIT_ROTOSCOPE_OPTIONS.blurRadius ?? 3;
const TIER_COLOR: Record<FocusTierName, string> = {
  face: "#1e88e5",
  body: "#fb8c00",
  background: "#6d6d6d",
};
const TIER_LABEL: Record<FocusTierName, string> = {
  face: "Face",
  body: "Body",
  background: "Background",
};

const usePrefersReducedMotion = (): boolean => {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
};

const formatScore = (value: number): string => {
  if (!Number.isFinite(value)) return "0";
  if (Math.abs(value) >= 100) return String(Math.round(value));
  return String(Math.round(value * 10) / 10);
};

const Bar = ({
  label,
  value,
  color,
  max = 255,
  display,
}: {
  label: string;
  value: number;
  color: string;
  max?: number;
  display?: string;
}) => (
  <div className={styles.walkBar}>
    <div className={styles.walkBarTrack}>
      <span
        className={styles.walkBarFill}
        style={{
          height: `${Math.round((Math.min(max, Math.max(0, value)) / max) * 100)}%`,
          background: color,
        }}
      />
    </div>
    <span className={styles.walkBarLabel}>{label}</span>
    <span className={styles.walkBarValue}>{display ?? value}</span>
  </div>
);

const KernelGrid = ({
  values,
  label,
  mode = "weights",
  winner,
  format,
}: {
  values: ReadonlyArray<ReadonlyArray<number>>;
  label: string;
  mode?: "weights" | "luma" | "heat";
  winner?: { x: number; y: number } | null;
  format?: (value: number) => string;
}) => {
  const peak = Math.max(1, ...values.flat().map((value) => Math.abs(value)));
  return (
    <div
      className={styles.walkKernel}
      aria-label={label}
      style={{ gridTemplateColumns: `repeat(${values[0]?.length ?? 1}, minmax(0, 1fr))` }}
    >
      {values.map((row, y) =>
        row.map((value, x) => {
          const heat = Math.round((Math.abs(value) / peak) * 255);
          return (
            <span
              key={`${label}-${y}-${x}`}
              className={styles.walkKernelCell}
              data-center={
                y === Math.floor(values.length / 2) && x === Math.floor(row.length / 2)
                  ? "true"
                  : undefined
              }
              data-winner={winner && winner.x === x && winner.y === y ? "true" : undefined}
              style={
                mode === "luma"
                  ? { background: `rgb(${value}, ${value}, ${value})` }
                  : mode === "heat"
                    ? {
                        background: `rgb(${heat}, ${heat}, ${heat})`,
                        color: heat > 140 ? "#111" : "#fafafa",
                      }
                    : undefined
              }
            >
              {format ? format(value) : value}
            </span>
          );
        }),
      )}
    </div>
  );
};

const LeftFrame = ({
  src,
  alt,
  x,
  y,
  width,
  height,
  radius,
  onPick,
}: {
  src: string;
  alt: string;
  x: number;
  y: number;
  width: number;
  height: number;
  radius: number;
  onPick: (nx: number, ny: number) => void;
}) => {
  const left = ((x + 0.5) / width) * 100;
  const top = ((y + 0.5) / height) * 100;
  return (
    <button
      type="button"
      className={styles.walkFrame}
      onClick={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect();
        onPick(
          (event.clientX - bounds.left) / bounds.width,
          (event.clientY - bounds.top) / bounds.height,
        );
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={alt} width={960} height={720} />
      {radius > 0 ? (
        <span
          className={styles.walkNeighborhood}
          style={{
            width: `${((radius * 2 + 1) / width) * 100}%`,
            height: `${((radius * 2 + 1) / height) * 100}%`,
            left: `${left}%`,
            top: `${top}%`,
          }}
        />
      ) : null}
      <span className={styles.walkCursor} style={{ left: `${left}%`, top: `${top}%` }} />
    </button>
  );
};

export default function MarkerWalkthrough({
  source,
  blurRadius = BLUR_RADIUS,
}: {
  source?: RgbaBuffer;
  blurRadius?: number;
}) {
  const { ref, inView } = useInView({ threshold: 0.35, fallbackInView: true });
  const reducedMotion = usePrefersReducedMotion();
  const [fields, setFields] = useState<MarkerFields | null>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [sampleIndex, setSampleIndex] = useState(0);
  const [picked, setPicked] = useState<{ x: number; y: number } | null>(null);
  const [paused, setPaused] = useState(false);
  const resumeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const step: StepId = STEPS[stepIndex].id;
  const focus = PORTRAIT_ROTOSCOPE_OPTIONS.focus!;

  useEffect(() => {
    if (source) {
      setFields(prepareMarkerFields(source, blurRadius, PORTRAIT_ROTOSCOPE_OPTIONS));
      return;
    }
    let cancelled = false;
    const image = new Image();
    image.src = "/rotoscope-portrait.jpg";
    image.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = PORTRAIT_PROCESSING_SIZE.width;
      canvas.height = PORTRAIT_PROCESSING_SIZE.height;
      const context = canvas.getContext("2d");
      if (!context) return;
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      if (!cancelled) {
        setFields(
          prepareMarkerFields(
            { width: canvas.width, height: canvas.height, rgba: pixels },
            blurRadius,
            PORTRAIT_ROTOSCOPE_OPTIONS,
          ),
        );
      }
    };
    return () => {
      cancelled = true;
    };
  }, [source, blurRadius]);

  const pause = useCallback(() => {
    setPaused(true);
    if (resumeTimer.current) clearTimeout(resumeTimer.current);
    resumeTimer.current = setTimeout(() => setPaused(false), IDLE_RESUME_MS);
  }, []);

  useEffect(
    () => () => {
      if (resumeTimer.current) clearTimeout(resumeTimer.current);
    },
    [],
  );

  const playing = inView && !paused && !reducedMotion && !picked;
  useEffect(() => {
    if (!playing) return;
    const pixels = window.setInterval(() => {
      setSampleIndex((index) => (index + 1) % SAMPLE_POINTS.length);
    }, PIXEL_DWELL_MS);
    return () => {
      window.clearInterval(pixels);
    };
  }, [playing]);

  const width = fields?.width ?? PORTRAIT_PROCESSING_SIZE.width;
  const height = fields?.height ?? PORTRAIT_PROCESSING_SIZE.height;
  const sample = SAMPLE_POINTS[sampleIndex];
  const rawX = picked ? picked.x : Math.round(sample.x * (width - 1));
  const rawY = picked ? picked.y : Math.round(sample.y * (height - 1));
  const x = clampInterior(rawX, width);
  const y = clampInterior(rawY, height);

  const tensor = useMemo(
    () => (fields ? shiTomasiAt(fields, x, y) : null),
    [fields, x, y],
  );
  const local = useMemo(
    () => (fields ? localMaxAt(fields.scores, width, height, x, y) : null),
    [fields, width, height, x, y],
  );
  const pixel = useMemo(
    () => (fields ? markerPixelAt(fields, x, y, focus) : null),
    [fields, x, y, focus],
  );

  const pick = (nx: number, ny: number) => {
    pause();
    setPicked({
      x: Math.round(nx * (width - 1)),
      y: Math.round(ny * (height - 1)),
    });
  };

  const jump = (index: number) => {
    pause();
    setStepIndex(index);
  };

  const location = picked ? `pixel ${x}, ${y}` : sample.label;
  const scorePeak = Math.max(
    1,
    tensor?.score ?? 0,
    ...(local?.window.flat() ?? [0]),
  );

  const viz = (() => {
    switch (step) {
      case "score":
        return (
          <>
            <p className={styles.walkFormula}>
              3×3 of Gx and Gy on the difference. Smaller eigenvalue is the
              corner score.
            </p>
            <div className={styles.walkKernels}>
              <div>
                <p className={styles.walkKernelTitle}>Gx</p>
                <KernelGrid
                  values={tensor?.gxWindow ?? []}
                  label="Shi-Tomasi Gx window"
                />
              </div>
              <div>
                <p className={styles.walkKernelTitle}>Gy</p>
                <KernelGrid
                  values={tensor?.gyWindow ?? []}
                  label="Shi-Tomasi Gy window"
                />
              </div>
            </div>
            <p className={styles.walkFormula}>
              Ix² {formatScore(tensor?.xx ?? 0)}, Ixy {formatScore(tensor?.xy ?? 0)},
              Iy² {formatScore(tensor?.yy ?? 0)}
            </p>
            <div className={styles.walkBars} aria-label="Shi-Tomasi corner score">
              <Bar
                label="λmin"
                value={tensor?.score ?? 0}
                max={scorePeak}
                color="#111"
                display={formatScore(tensor?.score ?? 0)}
              />
            </div>
          </>
        );
      case "maxima":
        return (
          <>
            <p className={styles.walkFormula}>
              Keep the pixel only if no neighbor has a higher score. Equal scores
              keep the smaller index.
            </p>
            <KernelGrid
              values={local?.window ?? []}
              label="Shi-Tomasi scores around the pixel"
              mode="heat"
              format={formatScore}
              winner={
                local?.winner
                  ? { x: local.winner.dx + 1, y: local.winner.dy + 1 }
                  : null
              }
            />
            <p className={styles.walkVerdict} data-kept={local?.kept ? "true" : "false"}>
              {local?.kept
                ? `Kept. Score ${formatScore(local.score)} is a local maximum.`
                : `Rejected. Neighbor (${local?.winner?.dx ?? 0}, ${local?.winner?.dy ?? 0}) is stronger.`}
            </p>
          </>
        );
      case "tiers":
        return (
          <>
            <p className={styles.walkFormula}>
              Face, body, and background each own a slice of the 1,600-marker
              budget. Spacing is a diamond so two markers cannot sit too close.
            </p>
            <ul className={styles.walkTiers} aria-label="Focus tiers">
              {(["face", "body", "background"] as const).map((tier) => (
                <li
                  key={tier}
                  className={styles.walkTier}
                  data-active={pixel?.tier === tier ? "true" : undefined}
                >
                  <i style={{ background: TIER_COLOR[tier] }} />
                  <span>
                    {TIER_LABEL[tier]}{" "}
                    {Math.round((fields?.quotaFractions[tier] ?? 0) * 100)}%
                  </span>
                  <span>spacing {fields?.spacing[tier] ?? 0}</span>
                </li>
              ))}
            </ul>
            <p className={styles.walkVerdict}>
              This pixel is {pixel ? TIER_LABEL[pixel.tier].toLowerCase() : "…"}.{" "}
              {pixel?.reason}
            </p>
          </>
        );
      default: {
        const _never: never = step;
        return _never;
      }
    }
  })();

  const frames =
    step === "score"
      ? [
          {
            src: "/rotoscope-difference.webp",
            alt: "Difference image that Shi-Tomasi scores",
            radius: 1,
          },
        ]
      : step === "maxima"
        ? [
            {
              src: "/rotoscope-shi-tomasi.webp",
              alt: "Shi-Tomasi score field stretched for display",
              radius: 1,
            },
          ]
        : [
            {
              src: "/rotoscope-focus.webp",
              alt: "Focus map: blue periocular ellipse, orange body polygon, gray background",
              radius: 0,
            },
            {
              src: "/rotoscope-markers.webp",
              alt: "Selected markers colored by face, body, and background",
              radius: 0,
            },
          ];

  return (
    <figure
      ref={ref}
      className={styles.walk}
      aria-label="Pixel walkthrough of marker selection"
    >
      <figcaption className={styles.walkCaption}>
        {STEPS[stepIndex].label} at the {location}
      </figcaption>
      <ol className={styles.walkSteps} aria-label="Marker-selection steps">
        {STEPS.map((item, index) => (
          <li key={item.id}>
            <button
              type="button"
              className={styles.walkStep}
              aria-label={`Show ${item.label} step`}
              aria-pressed={index === stepIndex}
              onClick={() => jump(index)}
            >
              {item.label}
            </button>
          </li>
        ))}
      </ol>
      <div className={styles.walkLayout}>
        <div className={styles.walkImages}>
          {frames.map((frame) => (
            <LeftFrame
              key={`${step}-${frame.src}`}
              src={frame.src}
              alt={frame.alt}
              x={x}
              y={y}
              width={width}
              height={height}
              radius={frame.radius}
              onPick={pick}
            />
          ))}
        </div>
        <div className={styles.walkViz}>{viz}</div>
      </div>
    </figure>
  );
}
