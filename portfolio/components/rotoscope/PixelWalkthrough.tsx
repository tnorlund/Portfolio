import React, { useEffect, useState } from "react";
import {
  PORTRAIT_PROCESSING_SIZE,
  PORTRAIT_ROTOSCOPE_OPTIONS,
} from "../home/Rotoscope/portraitConfig";
import styles from "../../styles/Rotoscope.module.css";
import {
  clampCoord,
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

const RADIUS = 2; // every stage shows the same 5×5 window
const BLUR_RADIUS = PORTRAIT_ROTOSCOPE_OPTIONS.blurRadius ?? 3;

const SAMPLE_POINTS = [
  { x: 0.367, y: 0.518, label: "left eye" },
  { x: 0.41, y: 0.62, label: "mouth" },
  { x: 0.33, y: 0.44, label: "hair" },
  { x: 0.78, y: 0.3, label: "background" },
] as const;

const inkFor = (red: number, green: number, blue: number): string =>
  rec601Gray(red, green, blue) > 140 ? "#111" : "#f4f4f4";

interface Cell {
  red: number;
  green: number;
  blue: number;
  text?: string;
}

const lumaCells = (window: ReadonlyArray<ReadonlyArray<number>>): Cell[][] =>
  window.map((row) =>
    row.map((value) => ({
      red: value,
      green: value,
      blue: value,
      text: String(value),
    })),
  );

const PixelGrid = ({ cells, label }: { cells: Cell[][]; label: string }) => {
  const size = cells.length;
  const mid = Math.floor(size / 2);
  return (
    <div
      className={styles.storyGrid}
      role="img"
      aria-label={label}
      style={{ gridTemplateColumns: `repeat(${size || 1}, minmax(0, 1fr))` }}
    >
      {cells.map((row, y) =>
        row.map((cell, x) => (
          <span
            key={`${label}-${y}-${x}`}
            className={styles.storyCell}
            data-center={x === mid && y === mid ? "true" : undefined}
            style={{
              background: `rgb(${cell.red}, ${cell.green}, ${cell.blue})`,
              color: inkFor(cell.red, cell.green, cell.blue),
            }}
          >
            {cell.text}
          </span>
        )),
      )}
    </div>
  );
};

const KernelGrid = ({
  values,
  label,
}: {
  values: ReadonlyArray<ReadonlyArray<number>>;
  label: string;
}) => (
  <div
    className={styles.storyKernel}
    role="img"
    aria-label={label}
    style={{ gridTemplateColumns: `repeat(${values[0]?.length ?? 1}, minmax(0, 1fr))` }}
  >
    {values.map((row, y) =>
      row.map((value, x) => (
        <span key={`${label}-${y}-${x}`} className={styles.storyKernelCell}>
          {value}
        </span>
      )),
    )}
  </div>
);

const DownArrow = () => (
  <svg className={styles.storyArrow} viewBox="0 0 24 34" aria-hidden="true">
    <path
      d="M12 2V29M4 21L12 29L20 21"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

const StoryCard = ({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) => (
  <div className={styles.storyCard}>
    <p className={styles.storyTitle}>{title}</p>
    {children}
  </div>
);

export default function PixelWalkthrough({
  source: injected,
  blurRadius = BLUR_RADIUS,
}: {
  source?: RgbaBuffer;
  blurRadius?: number;
}) {
  const [fields, setFields] = useState<PixelFields | null>(null);
  const [sampleIndex, setSampleIndex] = useState(0);

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

  const width = fields?.width ?? PORTRAIT_PROCESSING_SIZE.width;
  const height = fields?.height ?? PORTRAIT_PROCESSING_SIZE.height;
  const sample = SAMPLE_POINTS[sampleIndex];
  const x = clampCoord(Math.round(sample.x * (width - 1)), width - 1);
  const y = clampCoord(Math.round(sample.y * (height - 1)), height - 1);
  const windowSize = blurRadius * 2 + 1;

  const [red, green, blue] = fields
    ? sampleRgba(fields, x, y)
    : ([0, 0, 0] as const);
  const gray = rec601Gray(red, green, blue);
  const blurred = fields ? sampleField(fields.blurred, width, height, x, y) : 0;
  const difference = fields
    ? sampleField(fields.difference, width, height, x, y)
    : 0;
  const differenceSobel = fields
    ? sobelAt(fields.difference, fields.differenceSobel, width, height, x, y)
    : null;
  const gx = differenceSobel?.gx ?? 0;
  const gy = differenceSobel?.gy ?? 0;
  const magnitude = differenceSobel?.magnitude ?? 0;

  const colorCells: Cell[][] = fields
    ? rgbaNeighborhood(fields, x, y, RADIUS)
    : [];
  const grayCells = fields
    ? lumaCells(neighborhood(fields.gray, width, height, x, y, RADIUS))
    : [];
  const blurCells = fields
    ? lumaCells(neighborhood(fields.blurred, width, height, x, y, RADIUS))
    : [];
  const differenceCells = fields
    ? lumaCells(neighborhood(fields.difference, width, height, x, y, RADIUS))
    : [];

  return (
    <figure
      className={styles.story}
      aria-label="One pixel window followed through the grayscale chain"
    >
      <figcaption className={styles.walkCaption}>
        A 5×5 window at the {sample.label}, followed through the whole chain
      </figcaption>
      <div className={styles.storyPhoto}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/rotoscope-portrait.jpg"
          alt={`The original portrait; the marked window at the ${sample.label} is traced below`}
          width={PORTRAIT_PROCESSING_SIZE.width}
          height={PORTRAIT_PROCESSING_SIZE.height}
        />
        <span
          className={styles.storyMark}
          style={{ left: `${sample.x * 100}%`, top: `${sample.y * 100}%` }}
        />
      </div>
      <ul className={styles.storyChips} aria-label="Pick a window to follow">
        {SAMPLE_POINTS.map((point, index) => (
          <li key={point.label}>
            <button
              type="button"
              className={styles.storyChip}
              aria-pressed={index === sampleIndex}
              onClick={() => setSampleIndex(index)}
            >
              {point.label}
            </button>
          </li>
        ))}
      </ul>
      {fields ? (
        <div className={styles.storyFlow}>
          <DownArrow />
          <StoryCard title="The pixels">
            <PixelGrid cells={colorCells} label="The 25 source pixels" />
            <p className={styles.storyMath}>
              <span
                className={styles.storySwatch}
                style={{ background: `rgb(${red}, ${green}, ${blue})` }}
              />
              center pixel&ensp;R {red} · G {green} · B {blue}
            </p>
          </StoryCard>
          <DownArrow />
          <StoryCard title="Grayscale">
            <PixelGrid cells={grayCells} label="The same pixels as gray values" />
            <p className={styles.storyMath}>
              (77·{red} + 150·{green} + 29·{blue} + 128) ≫ 8 = {gray}
            </p>
          </StoryCard>
          <DownArrow />
          <StoryCard title="Box blur">
            <PixelGrid
              cells={blurCells}
              label="The same window after the box blur"
            />
            <p className={styles.storyMath}>
              each pixel → mean of its {windowSize}×{windowSize} neighborhood, so{" "}
              {gray} → {blurred}
            </p>
          </StoryCard>
          <DownArrow />
          <StoryCard title="Difference">
            <PixelGrid
              cells={differenceCells}
              label="Gray minus blur, the texture that remains"
            />
            <p className={styles.storyMath}>
              |{gray} − {blurred}| = {difference}
            </p>
          </StoryCard>
          <DownArrow />
          <StoryCard title="Sobel">
            <div className={styles.storyRow}>
              <div>
                <p className={styles.storyKernelTitle}>3×3 difference</p>
                <PixelGrid
                  cells={lumaCells(differenceSobel?.window ?? [])}
                  label="Difference neighborhood"
                />
              </div>
              <div>
                <p className={styles.storyKernelTitle}>Gx</p>
                <KernelGrid
                  values={SOBEL_X.map((row) => [...row])}
                  label="Sobel Gx kernel"
                />
              </div>
              <div>
                <p className={styles.storyKernelTitle}>Gy</p>
                <KernelGrid
                  values={SOBEL_Y.map((row) => [...row])}
                  label="Sobel Gy kernel"
                />
              </div>
            </div>
            <p className={styles.storyMath}>
              Gx {gx} · Gy {gy} → (|{gx}| + |{gy}| + 2) ≫ 2 = {magnitude}
            </p>
            <p className={styles.storyNote}>
              The same two kernels also run on the plain gray image; the
              watershed flood later climbs that copy.
            </p>
          </StoryCard>
        </div>
      ) : null}
    </figure>
  );
}
