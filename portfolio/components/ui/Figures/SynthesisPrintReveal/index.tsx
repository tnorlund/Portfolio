import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useInView } from "react-intersection-observer";
import {
  buildLabelBoxes,
  familiesIn,
  familyColors,
  ShowcaseLabelFile,
} from "../AugmentationShowcase/labelGeometry";
import { ENTITY_DISPLAY_NAMES } from "../labelStyles";
import styles from "./SynthesisPrintReveal.module.css";

/**
 * A single from-scratch synthesized Sprouts receipt, revealed the way a
 * thermal printer would make it: start zoomed in on the glyph detail, print
 * top-to-bottom with the camera following the print head, zoom out to the
 * LayoutLM-figure size, then draw the ground-truth labels.
 *
 * The receipt is `generated.webp` (composed by the merchant-grammar engine,
 * arithmetic reconciles) and every timing lives in the PHASES table below.
 */

const SRC = "/synthetic-receipts/showcase/generated.webp";
const LABELS_SRC = "/synthetic-receipts/showcase/generated.labels.json";
const RENDER_W = 760;
const RENDER_H = 1140;

const PRINT_MS = 7000;
const SETTLE_MS = 600;
const ZOOM_MS = 1400;
const ZOOM_SCALE = 2.4;

type Phase = "idle" | "printing" | "zooming" | "labeled";

const usePrefersReducedMotion = (): boolean => {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
};

interface LabelSweepProps {
  labels: ShowcaseLabelFile;
  visible: boolean;
}

const LabelSweep: React.FC<LabelSweepProps> = ({ labels, visible }) => {
  const boxes = useMemo(() => buildLabelBoxes(labels), [labels]);
  const colors = useMemo(
    () => familyColors(familiesIn(labels)),
    [labels],
  );
  if (!visible) {
    return null;
  }
  return (
    <svg
      className={styles.overlay}
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      {boxes.map((box, order) => (
        <rect
          key={box.index}
          data-testid="reveal-label-box"
          data-family={box.family}
          className={styles.sweepBox}
          style={{ animationDelay: `${order * 35}ms` }}
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
  );
};

interface LegendProps {
  labels: ShowcaseLabelFile;
}

const Legend: React.FC<LegendProps> = ({ labels }) => {
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

const PHASE_CAPTIONS: Record<Phase, string> = {
  idle: "A receipt that never existed, about to be printed.",
  printing:
    "Synthesizing: glyphs cloned from real Sprouts receipts, printed line by line.",
  zooming: "One new Sprouts receipt, composed from the merchant's grammar.",
  labeled:
    "And because it was generated from labeled positions, its ground truth comes for free.",
};

const SynthesisPrintReveal: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.35,
    triggerOnce: true,
    fallbackInView: true,
  });
  const reducedMotion = usePrefersReducedMotion();

  const [phase, setPhase] = useState<Phase>("idle");
  const [labels, setLabels] = useState<ShowcaseLabelFile | null>(null);
  const [runId, setRunId] = useState(0);
  const stageRef = useRef<HTMLDivElement>(null);

  // Ground-truth labels prefetch (progressive enhancement).
  useEffect(() => {
    let cancelled = false;
    fetch(LABELS_SRC)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: ShowcaseLabelFile | null) => {
        if (!cancelled && data) {
          setLabels(data);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // Phase machine: printing -> zooming -> labeled.
  useEffect(() => {
    if (!inView) {
      return;
    }
    if (reducedMotion) {
      setPhase("labeled");
      return;
    }
    setPhase("printing");
    const zoomTimer = setTimeout(
      () => setPhase("zooming"),
      PRINT_MS + SETTLE_MS,
    );
    const labelTimer = setTimeout(
      () => setPhase("labeled"),
      PRINT_MS + SETTLE_MS + ZOOM_MS,
    );
    return () => {
      clearTimeout(zoomTimer);
      clearTimeout(labelTimer);
    };
  }, [inView, reducedMotion, runId]);

  const replay = useCallback(() => {
    setPhase("idle");
    // next tick re-enters the machine with fresh CSS animations
    requestAnimationFrame(() => setRunId((n) => n + 1));
  }, []);

  const zoomedIn = phase === "printing" || phase === "idle";

  return (
    <div
      ref={ref}
      id="synthesis-print-reveal"
      data-testid="synthesis-print-reveal"
      className={styles.container}
    >
      <header className={styles.header}>
        <span className={styles.eyebrow}>From-scratch synthesis</span>
        <h3 className={styles.headline}>
          Printing a receipt that never existed
        </h3>
      </header>

      <div ref={stageRef} className={styles.stage}>
        <div
          className={`${styles.camera} ${
            zoomedIn ? styles.cameraZoomed : styles.cameraWide
          }`}
          data-phase={phase}
        >
          <div className={styles.receiptFrame}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              key={runId}
              src={SRC}
              alt="Synthesized Sprouts receipt printing line by line"
              width={RENDER_W}
              height={RENDER_H}
              className={`${styles.receiptImage} ${
                phase === "printing" ? styles.printing : ""
              } ${phase === "idle" ? styles.unprinted : ""}`}
              style={{ animationDuration: `${PRINT_MS}ms` }}
            />
            {phase === "printing" ? (
              <div
                className={styles.printHead}
                style={{ animationDuration: `${PRINT_MS}ms` }}
                aria-hidden="true"
              />
            ) : null}
            {labels ? (
              <LabelSweep labels={labels} visible={phase === "labeled"} />
            ) : null}
          </div>
        </div>
      </div>

      <div className={styles.footer}>
        <p className={styles.caption} data-testid="phase-caption">
          {PHASE_CAPTIONS[phase]}
        </p>
        {phase === "labeled" && labels ? <Legend labels={labels} /> : null}
        {phase === "labeled" ? (
          <button
            type="button"
            className={styles.replayButton}
            onClick={replay}
          >
            Print it again
          </button>
        ) : null}
      </div>
    </div>
  );
};

export default SynthesisPrintReveal;
