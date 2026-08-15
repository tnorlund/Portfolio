import Link from "next/link";
import React, {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import type { RotoscopeOptions, TierValues } from "../home/Rotoscope/algorithm";
import { RotoscopeLabClient } from "./client";
import {
  DEFAULT_MARKER_EXPERIMENT,
  normalizeMarkerExperiment,
  quotaPercentages,
  type MarkerExperimentOptions,
  type MarkerStrategy,
  type NoiseKind,
} from "./labAlgorithm";
import type { RotoscopeLabRenderSuccess } from "./protocol";
import styles from "./RotoscopeLab.module.css";

type Quality = 320 | 480;
type ViewMode = "output" | "diagnostic";
type LabStatus = "idle" | "editing" | "rendering" | "ready" | "error";

interface LabSettings {
  quality: Quality;
  base: Partial<RotoscopeOptions>;
  experiment: MarkerExperimentOptions;
}

interface RenderSummary {
  markerCount: number;
  tierCounts: TierValues;
  elapsedMs: number;
  path: string;
  markerDigest: string;
  labelDigest: string;
}

// Kept local so the unlinked lab never becomes a shared dependency of the
// homepage bundle. These values describe the lab's one fixed portrait.
const LAB_PORTRAIT_SOURCES = {
  avif: "/rotoscope-portrait.avif",
  webp: "/rotoscope-portrait.webp",
  fallback: "/rotoscope-portrait.jpg",
} as const;

const LAB_PORTRAIT_OPTIONS: Partial<RotoscopeOptions> = {
  blurRadius: 9,
  markerBudget: 312,
  quotas: { face: 0.55, body: 0.3, background: 0.15 },
  spacing: { face: 2, body: 4, background: 8 },
  focus: {
    face: {
      centerX: 0.4,
      centerY: 0.56,
      radiusX: 0.14,
      radiusY: 0.27,
    },
    body: [
      [0.31, 0.67],
      [0.53, 0.66],
      [0.75, 0.82],
      [0.8, 1],
      [0, 1],
      [0, 0.84],
      [0.26, 0.72],
    ],
  },
};

interface RangeControlProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  display?: string;
  disabled?: boolean;
  onChange: (value: number) => void;
}

const RangeControl = ({
  label,
  value,
  min,
  max,
  step,
  display,
  disabled = false,
  onChange,
}: RangeControlProps) => {
  const inputId = useId();
  return (
    <div className={styles.rangeControl} data-disabled={disabled || undefined}>
      <label htmlFor={inputId}>{label}</label>
      <output aria-hidden="true">{display ?? value}</output>
      <input
        id={inputId}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
      />
    </div>
  );
};

interface SegmentedProps<T extends string> {
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}

const Segmented = <T extends string>({
  label,
  value,
  options,
  onChange,
}: SegmentedProps<T>) => (
  <div className={styles.segmentedRow}>
    <span>{label}</span>
    <div className={styles.segmented} role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  </div>
);

const Section = ({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) => (
  <fieldset className={styles.controlSection}>
    <legend>{title}</legend>
    {children}
  </fieldset>
);

const cloneTierValues = (values: TierValues | undefined, fallback: TierValues) => ({
  face: values?.face ?? fallback.face,
  body: values?.body ?? fallback.body,
  background: values?.background ?? fallback.background,
});

const initialSettings = (): LabSettings => ({
  quality: 480,
  base: {
    ...LAB_PORTRAIT_OPTIONS,
    quotas: cloneTierValues(LAB_PORTRAIT_OPTIONS.quotas, {
      face: 0.55,
      body: 0.3,
      background: 0.15,
    }),
    spacing: cloneTierValues(LAB_PORTRAIT_OPTIONS.spacing, {
      face: 2,
      body: 4,
      background: 8,
    }),
  },
  experiment: normalizeMarkerExperiment(DEFAULT_MARKER_EXPERIMENT),
});

const paintCanvas = (
  canvas: HTMLCanvasElement | null,
  width: number,
  height: number,
  bitmap?: ImageBitmap,
  pixelsBuffer?: ArrayBuffer,
): void => {
  if (!canvas) {
    bitmap?.close();
    return;
  }
  canvas.width = width;
  canvas.height = height;
  if (bitmap) {
    const bitmapContext = canvas.getContext("bitmaprenderer");
    if (bitmapContext) bitmapContext.transferFromImageBitmap(bitmap);
    else canvas.getContext("2d")?.drawImage(bitmap, 0, 0);
    bitmap.close();
    return;
  }
  if (pixelsBuffer) {
    const context = canvas.getContext("2d");
    context?.putImageData(
      new ImageData(new Uint8ClampedArray(pixelsBuffer), width, height),
      0,
      0,
    );
  }
};

const closeResult = (result: RotoscopeLabRenderSuccess): void => {
  result.outputBitmap?.close();
  result.diagnosticBitmap?.close();
};

const strategyOptions = [
  { value: "radial", label: "Radial" },
  { value: "features", label: "Best features" },
  { value: "hybrid", label: "Hybrid" },
] as const;

const noiseOptions = [
  { value: "none", label: "None" },
  { value: "white", label: "White" },
  { value: "value", label: "Value" },
  { value: "fbm", label: "Fractal" },
] as const;

export default function RotoscopeLab() {
  const imageRef = useRef<HTMLImageElement>(null);
  const outputRef = useRef<HTMLCanvasElement>(null);
  const diagnosticRef = useRef<HTMLCanvasElement>(null);
  const seedOverlayRef = useRef<HTMLCanvasElement>(null);
  const clientRef = useRef<RotoscopeLabClient | null>(null);
  const debounceTimeoutRef = useRef<number | null>(null);
  const settingsRef = useRef<LabSettings>(initialSettings());
  const desiredRevisionRef = useRef(0);
  const mountedRef = useRef(true);
  const [settings, setSettings] = useState<LabSettings>(() => settingsRef.current);
  const [imageReady, setImageReady] = useState(false);
  const [status, setStatus] = useState<LabStatus>("idle");
  const [viewMode, setViewMode] = useState<ViewMode>("output");
  const [showSeeds, setShowSeeds] = useState(true);
  const [markers, setMarkers] = useState<Uint32Array>(() => new Uint32Array());
  const [renderSize, setRenderSize] = useState({ width: 480, height: 360 });
  const [summary, setSummary] = useState<RenderSummary | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "copied">("idle");

  const mutateSettings = useCallback(
    (update: (current: LabSettings) => LabSettings) => {
      desiredRevisionRef.current += 1;
      setStatus("editing");
      setSettings((current) => {
        const next = update(current);
        settingsRef.current = next;
        return next;
      });
    },
    [],
  );

  const updateBase = useCallback(
    (patch: Partial<RotoscopeOptions>) => {
      mutateSettings((current) => ({
        ...current,
        base: { ...current.base, ...patch },
      }));
    },
    [mutateSettings],
  );

  const updateExperiment = useCallback(
    (patch: Partial<MarkerExperimentOptions>) => {
      mutateSettings((current) => ({
        ...current,
        experiment: { ...current.experiment, ...patch },
      }));
    },
    [mutateSettings],
  );

  const executeRender = useCallback(async () => {
    const image = imageRef.current;
    if (!image) return;
    const snapshot = settingsRef.current;
    const revision = desiredRevisionRef.current;
    const width = snapshot.quality;
    const height = Math.round((snapshot.quality * 3) / 4);
    const client = clientRef.current ?? new RotoscopeLabClient();
    clientRef.current = client;
    if (!client.available()) {
      setStatus("error");
      return;
    }
    setStatus("rendering");
    try {
      const result = await client.render({
        image,
        width,
        height,
        baseOptions: snapshot.base,
        experiment: snapshot.experiment,
      });
      if (!result || !mountedRef.current) {
        if (result) closeResult(result);
        return;
      }
      if (revision !== desiredRevisionRef.current) {
        closeResult(result);
        return;
      }
      paintCanvas(
        outputRef.current,
        result.width,
        result.height,
        result.outputBitmap,
        result.outputPixelsBuffer,
      );
      paintCanvas(
        diagnosticRef.current,
        result.width,
        result.height,
        result.diagnosticBitmap,
        result.diagnosticPixelsBuffer,
      );
      setMarkers(new Uint32Array(result.markerIndicesBuffer));
      setRenderSize({ width: result.width, height: result.height });
      setSummary({
        markerCount: result.markerCount,
        tierCounts: result.tierCounts,
        elapsedMs: result.timings.totalMs,
        path: result.path,
        markerDigest: result.markerDigest,
        labelDigest: result.labelDigest,
      });
      setStatus("ready");
    } catch {
      if (mountedRef.current && revision === desiredRevisionRef.current) {
        setStatus("error");
      }
    }
  }, []);

  const settingsKey = useMemo(() => JSON.stringify(settings), [settings]);

  useEffect(() => {
    if (!imageReady) return;
    const timeout = window.setTimeout(() => {
      debounceTimeoutRef.current = null;
      void executeRender();
    }, 120);
    debounceTimeoutRef.current = timeout;
    return () => {
      window.clearTimeout(timeout);
      if (debounceTimeoutRef.current === timeout) {
        debounceTimeoutRef.current = null;
      }
    };
  }, [executeRender, imageReady, settingsKey]);

  const renderNow = useCallback(() => {
    if (debounceTimeoutRef.current !== null) {
      window.clearTimeout(debounceTimeoutRef.current);
      debounceTimeoutRef.current = null;
    }
    void executeRender();
  }, [executeRender]);

  useEffect(() => {
    const canvas = seedOverlayRef.current;
    if (!canvas) return;
    canvas.width = renderSize.width;
    canvas.height = renderSize.height;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (!showSeeds) return;
    context.fillStyle = "rgba(255, 255, 255, 0.94)";
    context.strokeStyle = "rgba(18, 23, 31, 0.82)";
    context.lineWidth = 1;
    for (let marker = 0; marker < markers.length; marker += 1) {
      const index = markers[marker];
      const y = Math.floor(index / renderSize.width);
      const x = index - y * renderSize.width;
      context.beginPath();
      context.arc(x + 0.5, y + 0.5, 2.1, 0, Math.PI * 2);
      context.fill();
      context.stroke();
    }
    if (settings.experiment.strategy !== "features") {
      const center = settings.experiment.radial;
      const centerX = center.centerX * renderSize.width;
      const centerY = center.centerY * renderSize.height;
      context.lineWidth = 1.5;
      context.strokeStyle = "rgba(255, 255, 255, 0.98)";
      context.beginPath();
      context.moveTo(centerX - 9, centerY);
      context.lineTo(centerX + 9, centerY);
      context.moveTo(centerX, centerY - 9);
      context.lineTo(centerX, centerY + 9);
      context.stroke();
      context.beginPath();
      context.arc(centerX, centerY, 5, 0, Math.PI * 2);
      context.stroke();
    }
  }, [
    markers,
    renderSize,
    settings.experiment.radial,
    settings.experiment.strategy,
    showSeeds,
  ]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clientRef.current?.dispose();
    };
  }, []);

  useEffect(() => {
    if (imageRef.current?.complete) setImageReady(true);
  }, []);

  const updateTier = (
    field: "quotas" | "spacing",
    tier: keyof TierValues,
    value: number,
  ) => {
    const fallback =
      field === "quotas"
        ? ({ face: 0.55, body: 0.3, background: 0.15 } as TierValues)
        : ({ face: 2, body: 4, background: 8 } as TierValues);
    const current = cloneTierValues(settings.base[field], fallback);
    updateBase({ [field]: { ...current, [tier]: value } });
  };

  const applyPreset = (preset: "features" | "bloom" | "ripple") => {
    if (preset === "features") {
      mutateSettings((current) => ({
        ...current,
        base: { ...current.base, markerBudget: 720 },
        experiment: normalizeMarkerExperiment({
          ...current.experiment,
          strategy: "features",
          noise: { ...current.experiment.noise, kind: "none", amount: 0 },
        }),
      }));
      return;
    }
    if (preset === "bloom") {
      mutateSettings((current) => ({
        ...current,
        base: { ...current.base, markerBudget: 420 },
        experiment: normalizeMarkerExperiment({
          ...current.experiment,
          strategy: "radial",
          radial: {
            ...current.experiment.radial,
            centerX: 0.4,
            centerY: 0.56,
            radiusX: 0.27,
            radiusY: 0.38,
            falloff: 2.8,
            coverage: 0.12,
          },
          noise: {
            ...current.experiment.noise,
            kind: "value",
            amount: 0.16,
            frequency: 5,
            seed: 982451653,
          },
        }),
      }));
      return;
    }
    mutateSettings((current) => ({
      ...current,
      base: { ...current.base, markerBudget: 640 },
      experiment: normalizeMarkerExperiment({
        ...current.experiment,
        strategy: "hybrid",
        hybridRadialWeight: 0.62,
        radial: { ...current.experiment.radial, coverage: 0.22 },
        noise: {
          ...current.experiment.noise,
          kind: "fbm",
          amount: 0.34,
          frequency: 3.2,
          octaves: 4,
          seed: 834821,
        },
      }),
    }));
  };

  const rerollSeed = () => {
    const values = new Uint32Array(1);
    if (typeof crypto !== "undefined") crypto.getRandomValues(values);
    else values[0] = Date.now() >>> 0;
    updateExperiment({
      noise: { ...settings.experiment.noise, seed: values[0] },
    });
  };

  const copySettings = async () => {
    await navigator.clipboard?.writeText(JSON.stringify(settings, null, 2));
    setCopyState("copied");
    window.setTimeout(() => setCopyState("idle"), 1200);
  };

  const onPreviewClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (settings.experiment.strategy === "features") return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const centerX = (event.clientX - bounds.left) / bounds.width;
    const centerY = (event.clientY - bounds.top) / bounds.height;
    updateExperiment({
      radial: {
        ...settings.experiment.radial,
        centerX,
        centerY,
      },
    });
  };

  const quotas = cloneTierValues(settings.base.quotas, {
    face: 0.55,
    body: 0.3,
    background: 0.15,
  });
  const spacing = cloneTierValues(settings.base.spacing, {
    face: 2,
    body: 4,
    background: 8,
  });
  const percentages = quotaPercentages(quotas);
  const radialDisabled = settings.experiment.strategy === "features";
  const noiseDisabled = settings.experiment.noise.kind === "none";
  const seedDisabled =
    noiseDisabled && settings.experiment.strategy === "features";
  const noiseScaleDisabled =
    noiseDisabled || settings.experiment.noise.kind === "white";

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <Link href="/" prefetch={false}>
          ← Back to portfolio
        </Link>
        <div>
          <h1>Rotoscope Lab</h1>
          <p>Explore Gaussian blue-noise marker distributions to shape region growth and detail.</p>
        </div>
      </header>

      <div className={styles.workspace}>
        <section className={styles.previewColumn} aria-label="Rotoscope preview">
          <div className={styles.previewToolbar}>
            <Segmented
              label="Preview"
              value={viewMode}
              options={[
                { value: "output", label: "Result" },
                { value: "diagnostic", label: "Basin map" },
              ]}
              onChange={setViewMode}
            />
            <label className={styles.checkbox}>
              <input
                type="checkbox"
                checked={showSeeds}
                onChange={(event) => setShowSeeds(event.currentTarget.checked)}
              />
              Show seeds
            </label>
          </div>
          <div
            className={styles.preview}
            data-view={viewMode}
            data-status={status}
            role="img"
            aria-label={
              viewMode === "output"
                ? "Interactive rotoscope result"
                : "Catchment basin diagnostic map"
            }
            onClick={onPreviewClick}
          >
            <picture>
              <source srcSet={LAB_PORTRAIT_SOURCES.avif} type="image/avif" />
              <source srcSet={LAB_PORTRAIT_SOURCES.webp} type="image/webp" />
              <img
                ref={imageRef}
                src={LAB_PORTRAIT_SOURCES.fallback}
                alt=""
                aria-hidden="true"
                width={480}
                height={360}
                onLoad={() => setImageReady(true)}
              />
            </picture>
            <canvas ref={outputRef} className={styles.outputCanvas} aria-hidden="true" />
            <canvas
              ref={diagnosticRef}
              className={styles.diagnosticCanvas}
              aria-hidden="true"
            />
            <canvas
              ref={seedOverlayRef}
              className={styles.seedCanvas}
              aria-hidden="true"
              data-crosshair={
                showSeeds && settings.experiment.strategy !== "features"
                  ? "visible"
                  : "hidden"
              }
            />
            <span className={styles.previewHint}>
              {radialDisabled ? "Feature score field" : "Click to move the radial origin"}
            </span>
          </div>

          <div className={styles.statusLine} aria-live="polite">
            <span data-status={status}>
              {status === "editing"
                ? "Editing — render queued"
                : status === "rendering"
                  ? "Rendering latest settings…"
                  : status === "error"
                    ? "The lab worker is unavailable"
                    : status === "ready"
                      ? "Rendered"
                      : "Preparing portrait…"}
            </span>
            {summary ? (
              <code title={`labels ${summary.labelDigest}`}>
                markers {summary.markerDigest}
              </code>
            ) : null}
          </div>

          <dl className={styles.metrics}>
            <div><dt>Basins</dt><dd>{summary?.markerCount ?? "—"}</dd></div>
            <div><dt>Face</dt><dd>{summary?.tierCounts.face ?? "—"}</dd></div>
            <div><dt>Body</dt><dd>{summary?.tierCounts.body ?? "—"}</dd></div>
            <div><dt>Background</dt><dd>{summary?.tierCounts.background ?? "—"}</dd></div>
            <div><dt>Render</dt><dd>{summary ? `${Math.round(summary.elapsedMs)} ms` : "—"}</dd></div>
            <div><dt>Worker</dt><dd>{summary?.path ?? "—"}</dd></div>
          </dl>
        </section>

        <aside className={styles.inspector} aria-label="Rotoscope controls">
          <div className={styles.inspectorActions}>
            <Segmented
              label="Quality"
              value={String(settings.quality) as "320" | "480"}
              options={[
                { value: "320", label: "Fast" },
                { value: "480", label: "Full" },
              ]}
              onChange={(value) =>
                mutateSettings((current) => ({
                  ...current,
                  quality: Number(value) as Quality,
                }))
              }
            />
            <button className={styles.primaryButton} type="button" onClick={renderNow}>
              Render now
            </button>
          </div>

          <Section title="Distribution">
            <Segmented
              label="Strategy"
              value={settings.experiment.strategy}
              options={strategyOptions}
              onChange={(strategy: MarkerStrategy) => updateExperiment({ strategy })}
            />
            <RangeControl
              label="Basins"
              value={settings.base.markerBudget ?? 720}
              min={48}
              max={1200}
              step={8}
              onChange={(markerBudget) => updateBase({ markerBudget })}
            />
            <RangeControl
              label="Origin X"
              value={settings.experiment.radial.centerX}
              min={0}
              max={1}
              step={0.01}
              display={settings.experiment.radial.centerX.toFixed(2)}
              disabled={radialDisabled}
              onChange={(centerX) =>
                updateExperiment({
                  radial: { ...settings.experiment.radial, centerX },
                })
              }
            />
            <RangeControl
              label="Origin Y"
              value={settings.experiment.radial.centerY}
              min={0}
              max={1}
              step={0.01}
              display={settings.experiment.radial.centerY.toFixed(2)}
              disabled={radialDisabled}
              onChange={(centerY) =>
                updateExperiment({
                  radial: { ...settings.experiment.radial, centerY },
                })
              }
            />
            <RangeControl
              label="Spread X"
              value={settings.experiment.radial.radiusX}
              min={0.08}
              max={1.2}
              step={0.01}
              display={settings.experiment.radial.radiusX.toFixed(2)}
              disabled={radialDisabled}
              onChange={(radiusX) =>
                updateExperiment({
                  radial: { ...settings.experiment.radial, radiusX },
                })
              }
            />
            <RangeControl
              label="Spread Y"
              value={settings.experiment.radial.radiusY}
              min={0.08}
              max={1.2}
              step={0.01}
              display={settings.experiment.radial.radiusY.toFixed(2)}
              disabled={radialDisabled}
              onChange={(radiusY) =>
                updateExperiment({
                  radial: { ...settings.experiment.radial, radiusY },
                })
              }
            />
            <RangeControl
              label="Center bias"
              value={settings.experiment.radial.falloff}
              min={0.25}
              max={8}
              step={0.05}
              display={settings.experiment.radial.falloff.toFixed(2)}
              disabled={radialDisabled}
              onChange={(falloff) =>
                updateExperiment({
                  radial: { ...settings.experiment.radial, falloff },
                })
              }
            />
            <RangeControl
              label="Coverage floor"
              value={settings.experiment.radial.coverage}
              min={0}
              max={0.75}
              step={0.01}
              display={`${Math.round(settings.experiment.radial.coverage * 100)}%`}
              disabled={radialDisabled}
              onChange={(coverage) =>
                updateExperiment({
                  radial: { ...settings.experiment.radial, coverage },
                })
              }
            />
            {settings.experiment.strategy === "hybrid" ? (
              <RangeControl
                label="Radial blend"
                value={settings.experiment.hybridRadialWeight}
                min={0}
                max={1}
                step={0.01}
                display={`${Math.round(settings.experiment.hybridRadialWeight * 100)}%`}
                onChange={(hybridRadialWeight) =>
                  updateExperiment({ hybridRadialWeight })
                }
              />
            ) : null}
          </Section>

          <Section title="Noise">
            <Segmented
              label="Algorithm"
              value={settings.experiment.noise.kind}
              options={noiseOptions}
              onChange={(kind: NoiseKind) =>
                updateExperiment({
                  noise: { ...settings.experiment.noise, kind },
                })
              }
            />
            <RangeControl
              label="Strength"
              value={settings.experiment.noise.amount}
              min={0}
              max={1}
              step={0.01}
              display={settings.experiment.noise.amount.toFixed(2)}
              disabled={noiseDisabled}
              onChange={(amount) =>
                updateExperiment({
                  noise: { ...settings.experiment.noise, amount },
                })
              }
            />
            <RangeControl
              label="Scale"
              value={settings.experiment.noise.frequency}
              min={1}
              max={24}
              step={0.1}
              display={settings.experiment.noise.frequency.toFixed(1)}
              disabled={noiseScaleDisabled}
              onChange={(frequency) =>
                updateExperiment({
                  noise: { ...settings.experiment.noise, frequency },
                })
              }
            />
            {settings.experiment.noise.kind === "fbm" ? (
              <>
                <RangeControl
                  label="Octaves"
                  value={settings.experiment.noise.octaves}
                  min={1}
                  max={6}
                  step={1}
                  onChange={(octaves) =>
                    updateExperiment({
                      noise: { ...settings.experiment.noise, octaves },
                    })
                  }
                />
                <RangeControl
                  label="Roughness"
                  value={settings.experiment.noise.gain}
                  min={0}
                  max={1}
                  step={0.01}
                  display={settings.experiment.noise.gain.toFixed(2)}
                  onChange={(gain) =>
                    updateExperiment({
                      noise: { ...settings.experiment.noise, gain },
                    })
                  }
                />
              </>
            ) : null}
            <div className={styles.seedControl}>
              <label htmlFor="rotoscope-lab-seed">Seed</label>
              <input
                id="rotoscope-lab-seed"
                type="number"
                min={0}
                max={0xffffffff}
                value={settings.experiment.noise.seed}
                disabled={seedDisabled}
                onChange={(event) =>
                  updateExperiment({
                    noise: {
                      ...settings.experiment.noise,
                      seed: Number(event.currentTarget.value),
                    },
                  })
                }
              />
              <button type="button" disabled={seedDisabled} onClick={rerollSeed}>
                Reroll
              </button>
            </div>
          </Section>

          <Section title="Focus allocation">
            {(["face", "body", "background"] as const).map((tier) => (
              <RangeControl
                key={tier}
                label={tier[0].toUpperCase() + tier.slice(1)}
                value={quotas[tier]}
                min={0}
                max={1}
                step={0.01}
                display={`${Math.round(percentages[tier])}%`}
                onChange={(value) => updateTier("quotas", tier, value)}
              />
            ))}
          </Section>

          <Section title="Spacing">
            <RangeControl label="Face" value={spacing.face} min={1} max={16} step={1} onChange={(value) => updateTier("spacing", "face", value)} />
            <RangeControl label="Body" value={spacing.body} min={1} max={24} step={1} onChange={(value) => updateTier("spacing", "body", value)} />
            <RangeControl label="Background" value={spacing.background} min={1} max={32} step={1} onChange={(value) => updateTier("spacing", "background", value)} />
            <RangeControl label="Feature scale" value={settings.base.blurRadius ?? 9} min={1} max={32} step={1} disabled={settings.experiment.strategy === "radial"} onChange={(blurRadius) => updateBase({ blurRadius })} />
          </Section>

          <div className={styles.presets}>
            <span>Presets</span>
            <div>
              <button type="button" onClick={() => applyPreset("features")}>Best features</button>
              <button type="button" onClick={() => applyPreset("bloom")}>Face bloom</button>
              <button type="button" onClick={() => applyPreset("ripple")}>Organic ripple</button>
            </div>
          </div>

          <div className={styles.footerActions}>
            <button
              type="button"
              onClick={() => {
                const reset = initialSettings();
                mutateSettings(() => reset);
              }}
            >
              Reset
            </button>
            <button type="button" onClick={() => void copySettings()}>
              {copyState === "copied" ? "Copied" : "Copy settings"}
            </button>
          </div>
        </aside>
      </div>
    </main>
  );
}
