import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import {
  PORTRAIT_PROCESSING_SIZE,
  PORTRAIT_ROTOSCOPE_OPTIONS,
} from "../home/Rotoscope/portraitConfig";
import styles from "../../styles/Rotoscope.module.css";
import {
  neighborhood,
  preparePixelFields,
  rec601Gray,
  sampleField,
  sampleRgba,
  sobelAt,
  SOBEL_X,
  SOBEL_Y,
  type PixelFields,
  type RgbaBuffer,
} from "./pixelMath";

const STEPS = [
  { id: "gray", label: "Grayscale" },
  { id: "blur", label: "Box blur" },
  { id: "difference", label: "Difference" },
  { id: "sobel", label: "Sobel" },
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

const Bar = ({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) => (
  <div className={styles.walkBar}>
    <div className={styles.walkBarTrack}>
      <span
        className={styles.walkBarFill}
        style={{ height: `${Math.round((value / 255) * 100)}%`, background: color }}
      />
    </div>
    <span className={styles.walkBarLabel}>{label}</span>
    <span className={styles.walkBarValue}>{value}</span>
  </div>
);

const Meter = ({
  label,
  value,
  color,
  chip,
}: {
  label: string;
  value: number;
  color: string;
  chip?: string;
}) => (
  <div className={styles.walkMeter} data-chip={chip ? "true" : undefined}>
    {chip ? (
      <span className={styles.walkMeterChip} style={{ background: chip }} />
    ) : null}
    <span className={styles.walkMeterLabel}>{label}</span>
    <span className={styles.walkMeterTrack} aria-hidden="true">
      <span
        className={styles.walkMeterFill}
        style={{
          width: `${Math.round((value / 255) * 100)}%`,
          background: color,
        }}
      />
    </span>
    <span className={styles.walkMeterValue}>{value}</span>
  </div>
);

const KernelGrid = ({
  values,
  label,
  mode = "weights",
}: {
  values: ReadonlyArray<ReadonlyArray<number>>;
  label: string;
  mode?: "weights" | "luma";
}) => {
  const large = values.length > 3;
  return (
    <div
      className={styles.walkKernel}
      aria-label={label}
      data-large={large ? "true" : undefined}
      style={{ gridTemplateColumns: `repeat(${values[0]?.length ?? 1}, minmax(0, 1fr))` }}
    >
      {values.map((row, y) =>
        row.map((value, x) => (
          <span
            key={`${label}-${y}-${x}`}
            className={styles.walkKernelCell}
            data-center={
              y === Math.floor(values.length / 2) && x === Math.floor(row.length / 2)
                ? "true"
                : undefined
            }
            style={
              mode === "luma"
                ? { background: `rgb(${value}, ${value}, ${value})` }
                : undefined
            }
          >
            {large ? "" : value}
          </span>
        )),
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

export default function PixelWalkthrough({
  source,
  blurRadius = BLUR_RADIUS,
}: {
  source?: RgbaBuffer;
  blurRadius?: number;
}) {
  const { ref, inView } = useInView({ threshold: 0.35, fallbackInView: true });
  const reducedMotion = usePrefersReducedMotion();
  const [fields, setFields] = useState<PixelFields | null>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [sampleIndex, setSampleIndex] = useState(0);
  const [picked, setPicked] = useState<{ x: number; y: number } | null>(null);
  const [paused, setPaused] = useState(false);
  const resumeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const step: StepId = STEPS[stepIndex].id;

  useEffect(() => {
    if (source) {
      setFields(preparePixelFields(source, blurRadius));
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
          preparePixelFields(
            { width: canvas.width, height: canvas.height, rgba: pixels },
            blurRadius,
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
  const x = picked ? picked.x : Math.round(sample.x * (width - 1));
  const y = picked ? picked.y : Math.round(sample.y * (height - 1));

  const rgb = useMemo(
    () => (fields ? sampleRgba(fields, x, y) : ([0, 0, 0] as const)),
    [fields, x, y],
  );
  const gray = rec601Gray(rgb[0], rgb[1], rgb[2]);
  const blurred = fields ? sampleField(fields.blurred, width, height, x, y) : 0;
  const difference = fields ? sampleField(fields.difference, width, height, x, y) : 0;
  const blurWindow = fields
    ? neighborhood(fields.gray, width, height, x, y, blurRadius)
    : [];
  const graySobel = fields
    ? sobelAt(fields.gray, fields.graySobel, width, height, x, y)
    : null;
  const differenceSobel = fields
    ? sobelAt(fields.difference, fields.differenceSobel, width, height, x, y)
    : null;

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
  const kernelOnes = Array.from({ length: blurRadius * 2 + 1 }, () =>
    Array.from({ length: blurRadius * 2 + 1 }, () => 1),
  );

  const viz = (() => {
    switch (step) {
      case "gray":
        return (
          <>
            <div className={styles.walkLead}>
              <span
                className={styles.walkMeterChip}
                style={{ background: `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` }}
              />
              <span>pulled pixel</span>
            </div>
            <div className={styles.walkMeters} aria-label="Red, green, and blue of the pixel">
              <Meter label="R" value={rgb[0]} color="#d32f2f" />
              <Meter label="G" value={rgb[1]} color="#2e7d32" />
              <Meter label="B" value={rgb[2]} color="#1565c0" />
            </div>
            <p className={styles.walkFormula}>
              (77R + 150G + 29B + 128) ≫ 8
            </p>
            <div className={styles.walkMeters} aria-label="Resulting grayscale value">
              <Meter
                label="Gray"
                value={gray}
                color="#111"
                chip={`rgb(${gray}, ${gray}, ${gray})`}
              />
            </div>
          </>
        );
      case "blur":
        return (
          <>
            <p className={styles.walkFormula}>
              {blurRadius * 2 + 1}×{blurRadius * 2 + 1} box, radius {blurRadius}.
              Average the row, then the column.
            </p>
            <div className={styles.walkKernelPair}>
              <div>
                <p className={styles.walkKernelTitle}>Kernel</p>
                <KernelGrid values={kernelOnes} label="Box-blur kernel" />
              </div>
              <div>
                <p className={styles.walkKernelTitle}>Neighborhood</p>
                <KernelGrid
                  values={blurWindow}
                  label="Gray values under the kernel"
                  mode="luma"
                />
              </div>
            </div>
            <div className={styles.walkMeters} aria-label="Blurred grayscale value">
              <Meter
                label="Mean"
                value={blurred}
                color="#111"
                chip={`rgb(${blurred}, ${blurred}, ${blurred})`}
              />
            </div>
          </>
        );
      case "difference":
        return (
          <>
            <div
              className={styles.walkMeters}
              aria-label="Gray, blur, and absolute difference"
            >
              <Meter
                label="Gray"
                value={gray}
                color="#444"
                chip={`rgb(${gray}, ${gray}, ${gray})`}
              />
              <Meter
                label="Blur"
                value={blurred}
                color="#999"
                chip={`rgb(${blurred}, ${blurred}, ${blurred})`}
              />
              <Meter label="|Δ|" value={difference} color="#111" />
            </div>
            <p className={styles.walkFormula}>
              |{gray} − {blurred}| = {difference}
            </p>
          </>
        );
      case "sobel":
        return (
          <>
            <div className={styles.walkKernels}>
              <div>
                <p className={styles.walkKernelTitle}>Gx</p>
                <KernelGrid values={SOBEL_X.map((row) => [...row])} label="Sobel Gx kernel" />
              </div>
              <div>
                <p className={styles.walkKernelTitle}>Gy</p>
                <KernelGrid values={SOBEL_Y.map((row) => [...row])} label="Sobel Gy kernel" />
              </div>
            </div>
            <p className={styles.walkKernelTitle}>On the difference</p>
            <KernelGrid
              values={differenceSobel?.window ?? []}
              label="Difference neighborhood"
              mode="luma"
            />
            <p className={styles.walkFormula}>
              Gx {differenceSobel?.gx ?? 0}, Gy {differenceSobel?.gy ?? 0}, mag{" "}
              {differenceSobel?.magnitude ?? 0}
            </p>
            <p className={styles.walkKernelTitle}>On the source gray</p>
            <KernelGrid
              values={graySobel?.window ?? []}
              label="Gray neighborhood"
              mode="luma"
            />
            <p className={styles.walkFormula}>
              Gx {graySobel?.gx ?? 0}, Gy {graySobel?.gy ?? 0}, mag{" "}
              {graySobel?.magnitude ?? 0}
            </p>
            <div className={styles.walkBars} aria-label="Sobel magnitudes on both landscapes">
              <Bar label="Diff" value={differenceSobel?.magnitude ?? 0} color="#111" />
              <Bar label="Gray" value={graySobel?.magnitude ?? 0} color="#666" />
            </div>
          </>
        );
      default: {
        const _never: never = step;
        return _never;
      }
    }
  })();

  const frames =
    step === "gray"
      ? [{ src: "/rotoscope-portrait.jpg", alt: "The source portrait", radius: 0 }]
      : step === "blur"
        ? [
            {
              src: "/rotoscope-gray.webp",
              alt: "Rec. 601 grayscale of the source portrait",
              radius: blurRadius,
            },
          ]
        : step === "difference"
          ? [
              {
                src: "/rotoscope-gray.webp",
                alt: "Rec. 601 grayscale of the source portrait",
                radius: 0,
              },
              {
                src: "/rotoscope-blurred.webp",
                alt: "Low-frequency box blur of the grayscale portrait",
                radius: 0,
              },
            ]
          : [
              {
                src: "/rotoscope-difference.webp",
                alt: "Absolute difference between grayscale and its blur, stretched for display",
                radius: 1,
              },
              {
                src: "/rotoscope-gray.webp",
                alt: "Rec. 601 grayscale of the source portrait",
                radius: 1,
              },
            ];

  return (
    <figure ref={ref} className={styles.walk} aria-label="Pixel walkthrough of the grayscale chain">
      <figcaption className={styles.walkCaption}>
        {STEPS[stepIndex].label} at the {location}
      </figcaption>
      <ol className={styles.walkSteps} aria-label="Image-processing steps">
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
        <div className={styles.walkImages} data-pair={frames.length > 1 ? "true" : undefined}>
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
