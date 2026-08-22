import React, { useEffect, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { PORTRAIT_PROCESSING_SIZE } from "../home/Rotoscope/portraitConfig";
import styles from "../../styles/Rotoscope.module.css";
import { rec601Gray, sampleRgba, type RgbaBuffer } from "./pixelMath";

/**
 * The tracer rides a closed cubic-Bézier loop drawn in a 100×75 box, the
 * same 4:3 frame as the portrait, so one path works at every rendered size.
 */
export const TRACER_LOOP =
  "M 37 50 C 46 56 70 30 79 23 C 92 30 90 52 79 61 C 70 70 52 74 40 66 C 30 59 12 56 14 44 C 16 34 28 44 37 50 Z";
const LOOP_START = { x: 37, y: 50 };
const VIEW = { width: 100, height: 75 } as const;
const LOOP_MS = 18000;
const SAMPLE_MS = 120;
const REDUCE_QUERY = "(prefers-reduced-motion: reduce)";

const CHANNELS = [
  { key: "red", label: "R", rgb: "229, 57, 53" },
  { key: "green", label: "G", rgb: "67, 160, 71" },
  { key: "blue", label: "B", rgb: "30, 136, 229" },
] as const;

interface Sample {
  x: number;
  y: number;
  red: number;
  green: number;
  blue: number;
}

const usePrefersReducedMotion = (): boolean => {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(REDUCE_QUERY);
    setReduced(mq.matches);
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
};

/** Map a point in the 100×75 loop box to the pixel under it. */
export const samplePoint = (
  buffer: RgbaBuffer,
  point: { x: number; y: number },
): Sample => {
  const x = Math.round((point.x / VIEW.width) * (buffer.width - 1));
  const y = Math.round((point.y / VIEW.height) * (buffer.height - 1));
  const [red, green, blue] = sampleRgba(buffer, x, y);
  return { x, y, red, green, blue };
};

const Swatch = ({
  label,
  value,
  rgb,
}: {
  label: string;
  value: number;
  rgb: string;
}) => (
  <div className={styles.tracerSwatch} aria-label={`${label} ${value}`}>
    <span
      className={styles.tracerCircle}
      style={{ background: `rgba(${rgb}, ${value / 255})` }}
    />
    <span className={styles.tracerLabel}>{label}</span>
    <span className={styles.tracerValue}>{value}</span>
  </div>
);

export default function RgbTracer({ source: injected }: { source?: RgbaBuffer }) {
  const { ref: inViewRef, inView } = useInView({ threshold: 0.2, fallbackInView: true });
  const reducedMotion = usePrefersReducedMotion();
  const [buffer, setBuffer] = useState<RgbaBuffer | null>(injected ?? null);
  const [bw, setBw] = useState(false);
  const [point, setPoint] = useState(LOOP_START);
  const [sample, setSample] = useState<Sample | null>(null);
  const pathRef = useRef<SVGPathElement | null>(null);
  const dotRef = useRef<SVGCircleElement | null>(null);

  useEffect(() => {
    if (injected) {
      setBuffer(injected);
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
      const rgba = context.getImageData(0, 0, canvas.width, canvas.height).data;
      if (!cancelled) {
        setBuffer({ width: canvas.width, height: canvas.height, rgba });
      }
    };
    return () => {
      cancelled = true;
    };
  }, [injected]);

  useEffect(() => {
    if (!buffer) return;
    const path = pathRef.current;
    const length =
      path && typeof path.getTotalLength === "function" ? path.getTotalLength() : 0;
    if (!path || !length || reducedMotion || !inView) {
      setSample(samplePoint(buffer, LOOP_START));
      setPoint(LOOP_START);
      return;
    }
    let frame = 0;
    let lastSample = -Infinity;
    const start = performance.now();
    const tick = (now: number) => {
      const t = ((now - start) % LOOP_MS) / LOOP_MS;
      const at = path.getPointAtLength(t * length);
      dotRef.current?.setAttribute("cx", String(at.x));
      dotRef.current?.setAttribute("cy", String(at.y));
      if (now - lastSample >= SAMPLE_MS) {
        lastSample = now;
        const next = { x: at.x, y: at.y };
        setPoint(next);
        setSample(samplePoint(buffer, next));
      }
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [buffer, inView, reducedMotion]);

  const gray = sample ? rec601Gray(sample.red, sample.green, sample.blue) : 0;

  return (
    <figure
      ref={inViewRef}
      className={styles.tracer}
      aria-label="A tracer reading pixel values off the portrait"
    >
      <div className={styles.tracerPhoto} data-bw={bw ? "true" : undefined}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/rotoscope-portrait.jpg"
          alt={
            bw
              ? "The original portrait in black and white"
              : "The original portrait in color"
          }
          width={PORTRAIT_PROCESSING_SIZE.width}
          height={PORTRAIT_PROCESSING_SIZE.height}
        />
        <svg
          className={styles.tracerOverlay}
          viewBox={`0 0 ${VIEW.width} ${VIEW.height}`}
          aria-hidden="true"
        >
          <path ref={pathRef} className={styles.tracerPath} d={TRACER_LOOP} />
          <circle
            ref={dotRef}
            className={styles.tracerDot}
            cx={point.x}
            cy={point.y}
            r="1.6"
          />
        </svg>
      </div>
      <div className={styles.tracerControls}>
        <span className={styles.tracerSwitchLabel} aria-hidden="true">RGB</span>
        <button
          type="button"
          role="switch"
          aria-checked={bw}
          aria-label="Black and white"
          className={styles.tracerSwitch}
          onClick={() => setBw((value) => !value)}
        >
          <span className={styles.tracerKnob} />
        </button>
        <span className={styles.tracerSwitchLabel} aria-hidden="true">B/W</span>
      </div>
      <div className={styles.tracerReadout} aria-live="off">
        {sample ? (
          bw ? (
            <Swatch label="Gray" value={gray} rgb="var(--text-color-rgb)" />
          ) : (
            CHANNELS.map((channel) => (
              <Swatch
                key={channel.key}
                label={channel.label}
                value={sample[channel.key]}
                rgb={channel.rgb}
              />
            ))
          )
        ) : null}
      </div>
      <figcaption className={styles.tracerCaption}>
        {sample ? (
          bw ? (
            <>
              pixel {sample.x}, {sample.y} · (77·{sample.red} + 150·{sample.green} + 29·
              {sample.blue} + 128) ≫ 8 = {gray}
            </>
          ) : (
            <>
              pixel {sample.x}, {sample.y} · three numbers, each 0 to 255
            </>
          )
        ) : (
          "Reading the portrait…"
        )}
      </figcaption>
    </figure>
  );
}
