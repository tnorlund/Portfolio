import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import {
  PORTRAIT_PROCESSING_SIZE,
  PORTRAIT_ROTOSCOPE_OPTIONS,
} from "../home/Rotoscope/portraitConfig";
import styles from "../../styles/Rotoscope.module.css";
import {
  clampCoord,
  cropWindow,
  neighborhood,
  preparePixelFields,
  rec601Gray,
  rgbaNeighborhood,
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
type ZoomPhase = "photo" | "frame" | "zoom" | "pixels";

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
const ZOOM_PHOTO_MS = 800;
const ZOOM_FRAME_MS = 650;
const ZOOM_SCALE_MS = 1100;
const ZOOM_FILL = 0.88;
const MOBILE_QUERY = "(max-width: 768px)";
const REDUCE_QUERY = "(prefers-reduced-motion: reduce)";

const useMedia = (query: string): boolean => {
  const [matches, setMatches] = useState(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return false;
    }
    return window.matchMedia(query).matches;
  });
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    setMatches(mq.matches);
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, [query]);
  return matches;
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

const lumaCells = (window: ReadonlyArray<ReadonlyArray<number>>) =>
  window.map((row) =>
    row.map((value) => ({
      red: value,
      green: value,
      blue: value,
      label: value,
    })),
  );

const inkFor = (red: number, green: number, blue: number): string =>
  rec601Gray(red, green, blue) > 140 ? "#111" : "#f4f4f4";

const PixelBoard = ({
  cells,
  label,
  onPick,
}: {
  cells: ReadonlyArray<
    ReadonlyArray<{ red: number; green: number; blue: number; label: number }>
  >;
  label: string;
  onPick: (dx: number, dy: number) => void;
}) => {
  const size = cells.length;
  const mid = Math.floor(size / 2);
  return (
    <div
      className={styles.walkPixels}
      role="group"
      aria-label={label}
      data-size={size}
      style={{ gridTemplateColumns: `repeat(${size || 1}, minmax(0, 1fr))` }}
    >
      {cells.map((row, y) =>
        row.map((cell, x) => (
          <button
            key={`${label}-${y}-${x}`}
            type="button"
            className={styles.walkPixel}
            data-center={x === mid && y === mid ? "true" : undefined}
            aria-label={`${label}, offset ${x - mid}, ${y - mid}, value ${cell.label}`}
            style={{
              background: `rgb(${cell.red}, ${cell.green}, ${cell.blue})`,
              color: inkFor(cell.red, cell.green, cell.blue),
            }}
            onClick={() => onPick(x - mid, y - mid)}
          >
            {cell.label}
          </button>
        )),
      )}
    </div>
  );
};

const Board = ({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) => (
  <div className={styles.walkBoard}>
    <p className={styles.walkKernelTitle}>{title}</p>
    {children}
  </div>
);

const sizeLabel = (displaySize: number, fullSize: number): string =>
  displaySize === fullSize
    ? `${displaySize}×${displaySize}`
    : `${displaySize}×${displaySize} of ${fullSize}×${fullSize}`;

export default function PixelWalkthrough({
  source: injected,
  blurRadius = BLUR_RADIUS,
  skipIntro,
}: {
  source?: RgbaBuffer;
  blurRadius?: number;
  skipIntro?: boolean;
}) {
  const { ref, inView } = useInView({ threshold: 0.35, fallbackInView: true });
  const [mounted, setMounted] = useState(false);
  const compactQuery = useMedia(MOBILE_QUERY);
  const reducedMotion = useMedia(REDUCE_QUERY);
  const compact = mounted && compactQuery;
  const skipZoom = skipIntro ?? Boolean(injected);
  const [fields, setFields] = useState<PixelFields | null>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [sampleIndex, setSampleIndex] = useState(0);
  const [picked, setPicked] = useState<{ x: number; y: number } | null>(null);
  const [paused, setPaused] = useState(false);
  const [zoomPhase, setZoomPhase] = useState<ZoomPhase>(() =>
    skipZoom ? "pixels" : "photo",
  );
  const resumeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const zoomToken = useRef(0);
  const step: StepId = STEPS[stepIndex].id;

  useEffect(() => {
    if (injected) {
      setFields(preparePixelFields(injected, blurRadius));
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
  }, [injected, blurRadius]);

  const pause = useCallback(() => {
    setPaused(true);
    if (resumeTimer.current) clearTimeout(resumeTimer.current);
    resumeTimer.current = setTimeout(() => setPaused(false), IDLE_RESUME_MS);
  }, []);

  const playZoom = useCallback(() => {
    zoomToken.current += 1;
    const token = zoomToken.current;
    if (skipZoom || reducedMotion) {
      setZoomPhase("pixels");
      return;
    }
    setZoomPhase("photo");
    window.setTimeout(() => {
      if (zoomToken.current !== token) return;
      setZoomPhase("frame");
      window.setTimeout(() => {
        if (zoomToken.current !== token) return;
        setZoomPhase("zoom");
        window.setTimeout(() => {
          if (zoomToken.current !== token) return;
          setZoomPhase("pixels");
        }, ZOOM_SCALE_MS);
      }, ZOOM_FRAME_MS);
    }, ZOOM_PHOTO_MS);
  }, [reducedMotion, skipZoom]);

  useEffect(
    () => () => {
      if (resumeTimer.current) clearTimeout(resumeTimer.current);
      zoomToken.current += 1;
    },
    [],
  );

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted) return;
    if (skipZoom || reducedMotion) {
      setZoomPhase("pixels");
      return;
    }
    if (!inView) return;
    playZoom();
    return () => {
      zoomToken.current += 1;
    };
  }, [mounted, inView, playZoom, reducedMotion, skipZoom]);

  const playing =
    inView && !paused && !reducedMotion && !picked && zoomPhase === "pixels";
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
  const displayRadius = step === "sobel" ? 1 : compact ? 1 : blurRadius;
  const displaySize = displayRadius * 2 + 1;
  const windowSize = blurRadius * 2 + 1;

  const rgb = useMemo(
    () => (fields ? sampleRgba(fields, x, y) : ([0, 0, 0] as const)),
    [fields, x, y],
  );
  const gray = rec601Gray(rgb[0], rgb[1], rgb[2]);
  const blurred = fields ? sampleField(fields.blurred, width, height, x, y) : 0;
  const difference = fields ? sampleField(fields.difference, width, height, x, y) : 0;
  const colorWindow = fields ? rgbaNeighborhood(fields, x, y, blurRadius) : [];
  const blurWindow = fields
    ? neighborhood(fields.gray, width, height, x, y, blurRadius)
    : [];
  const blurredWindow = fields
    ? neighborhood(fields.blurred, width, height, x, y, blurRadius)
    : [];
  const graySobel = fields
    ? sobelAt(fields.gray, fields.graySobel, width, height, x, y)
    : null;
  const differenceSobel = fields
    ? sobelAt(fields.difference, fields.differenceSobel, width, height, x, y)
    : null;

  const pickCell = (dx: number, dy: number) => {
    pause();
    setPicked({
      x: clampCoord(x + dx, width - 1),
      y: clampCoord(y + dy, height - 1),
    });
  };

  const jump = (index: number) => {
    pause();
    setStepIndex(index);
  };

  const location = picked ? `pixel ${x}, ${y}` : sample.label;
  const sourceCells = cropWindow(
    colorWindow.map((row) =>
      row.map((cell) => ({
        ...cell,
        label: rec601Gray(cell.red, cell.green, cell.blue),
      })),
    ),
    displayRadius,
  );
  const grayCells = lumaCells(cropWindow(blurWindow, displayRadius));
  const blurCells = lumaCells(cropWindow(blurredWindow, displayRadius));
  const boardTitle = sizeLabel(displaySize, windowSize);
  const zoomScale = Math.max(1, (ZOOM_FILL * width) / displaySize);
  const originX = ((x + 0.5) / width) * 100;
  const originY = ((y + 0.5) / height) * 100;
  const rect = {
    left: `${((x - displayRadius) / width) * 100}%`,
    top: `${((y - displayRadius) / height) * 100}%`,
    width: `${(displaySize / width) * 100}%`,
    height: `${(displaySize / height) * 100}%`,
  };
  const scaled = zoomPhase === "zoom" || zoomPhase === "pixels";

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
            <p className={styles.walkFormula} aria-label="Box-blur kernel">
              {windowSize}×{windowSize} box, radius {blurRadius}. Average the
              row, then the column. All weights 1.
            </p>
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
            <p className={styles.walkFormula}>
              Difference Gx {differenceSobel?.gx ?? 0}, Gy {differenceSobel?.gy ?? 0},
              mag {differenceSobel?.magnitude ?? 0}
            </p>
            <p className={styles.walkFormula}>
              Gray Gx {graySobel?.gx ?? 0}, Gy {graySobel?.gy ?? 0}, mag{" "}
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

  const boards =
    step === "gray"
      ? [
          {
            title: `${boardTitle} source`,
            label: "Source pixels around the sample",
            cells: sourceCells,
          },
        ]
      : step === "blur"
        ? [
            {
              title: `${boardTitle} gray`,
              label: "Gray pixels under the box-blur kernel",
              cells: grayCells,
            },
          ]
        : step === "difference"
          ? [
              {
                title: `${boardTitle} gray`,
                label: "Gray pixels around the sample",
                cells: grayCells,
              },
              {
                title: `${boardTitle} blur`,
                label: "Blurred pixels around the sample",
                cells: blurCells,
              },
            ]
          : [
              {
                title: "3×3 difference",
                label: "Difference neighborhood",
                cells: lumaCells(differenceSobel?.window ?? []),
              },
              {
                title: "3×3 gray",
                label: "Gray neighborhood",
                cells: lumaCells(graySobel?.window ?? []),
              },
            ];

  const grid = (
    <div className={styles.walkImages} data-pair={boards.length > 1 ? "true" : undefined}>
      {boards.map((board) => (
        <Board key={`${step}-${board.label}`} title={board.title}>
          <PixelBoard cells={board.cells} label={board.label} onPick={pickCell} />
        </Board>
      ))}
    </div>
  );

  const caption =
    zoomPhase !== "pixels" && !skipZoom
      ? `Zooming from the photograph into the ${displaySize}×${displaySize} at the ${location}`
      : compact && step !== "sobel"
        ? `${STEPS[stepIndex].label} at the ${location} — ${displaySize}×${displaySize} center of the ${windowSize}×${windowSize} neighborhood`
        : `${STEPS[stepIndex].label} at the ${location}`;

  return (
    <figure ref={ref} className={styles.walk} aria-label="Pixel walkthrough of the grayscale chain">
      <figcaption className={styles.walkCaption}>{caption}</figcaption>
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
        {skipZoom ? (
          grid
        ) : (
          <div className={styles.walkStage}>
            <div
              className={styles.walkZoomStage}
              data-phase={zoomPhase}
              data-testid="walk-zoom-stage"
            >
              <div
                className={styles.walkZoomWorld}
                style={
                  {
                    "--zoom-x": `${originX}%`,
                    "--zoom-y": `${originY}%`,
                    "--zoom-scale": scaled ? String(zoomScale) : "1",
                  } as React.CSSProperties
                }
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src="/rotoscope-portrait.jpg"
                  alt="Original portrait; the highlighted window becomes the pixel grid"
                  width={PORTRAIT_PROCESSING_SIZE.width}
                  height={PORTRAIT_PROCESSING_SIZE.height}
                />
                <span className={styles.walkZoomRect} style={rect} />
              </div>
              <div
                className={styles.walkZoomGrid}
                aria-hidden={zoomPhase !== "pixels"}
              >
                {grid}
              </div>
            </div>
            <button
              className={styles.replayButton}
              type="button"
              onClick={() => {
                pause();
                playZoom();
              }}
            >
              Replay the zoom
            </button>
          </div>
        )}
        <div className={styles.walkViz}>{viz}</div>
      </div>
    </figure>
  );
}
