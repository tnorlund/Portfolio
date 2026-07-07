import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useInView } from "react-intersection-observer";
import { ENTITY_DISPLAY_NAMES } from "../labelStyles";
import {
  buildLabelBoxes,
  familiesIn,
  familyColors,
  findHighlightIndices,
  pickScrollTarget,
  ShowcaseLabelFile,
} from "./labelGeometry";
import {
  generatedVariantCount,
  labelsSrc,
  renderSrc,
  showcaseVariants,
  ShowcaseVariant,
} from "./showcaseData";
import styles from "./AugmentationShowcase.module.css";

/**
 * "Growing the training set" — interactive augmentation showcase.
 *
 * A real Sprouts receipt front-and-center; operation buttons swap in
 * engine-generated variants (add/remove line items with recomputed totals),
 * a labels toggle overlays the ground-truth token boxes drawn live from each
 * variant's labels.json, and a counter tracks the perfectly-labeled training
 * examples explored. Each variant discloses which real receipt it was
 * generated from (the variants do not all share the displayed base).
 */

const OPERATION_NAMES: Record<string, string> = {
  base_real_receipt: "Real receipt",
  add_line_item: "Add line item",
  remove_line_item: "Remove line item",
};

interface OperationControlsProps {
  activeId: string;
  onSelect: (variant: ShowcaseVariant) => void;
}

const OperationControls: React.FC<OperationControlsProps> = ({
  activeId,
  onSelect,
}) => (
  <div
    className={styles.controls}
    role="group"
    aria-label="Augmentation operations"
  >
    {showcaseVariants.map((variant) => (
      <button
        key={variant.id}
        type="button"
        className={styles.operationButton}
        aria-pressed={variant.id === activeId}
        onClick={() => onSelect(variant)}
      >
        {variant.controlLabel}
      </button>
    ))}
  </div>
);

interface LabelOverlayProps {
  labelFile: ShowcaseLabelFile;
  showLabels: boolean;
  highlightIndices: number[];
}

const LabelOverlay: React.FC<LabelOverlayProps> = ({
  labelFile,
  showLabels,
  highlightIndices,
}) => {
  const boxes = useMemo(() => buildLabelBoxes(labelFile), [labelFile]);
  const colors = useMemo(
    () => familyColors(familiesIn(labelFile)),
    [labelFile],
  );
  const highlights = useMemo(
    () => new Set(highlightIndices),
    [highlightIndices],
  );

  return (
    <svg
      className={styles.overlay}
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      {boxes.map((box) => {
        const isHighlight = highlights.has(box.index);
        if (!showLabels && !isHighlight) {
          return null;
        }
        const color = colors[box.family];
        return (
          <rect
            key={box.index}
            data-testid={isHighlight ? "highlight-box" : "label-box"}
            data-family={box.family}
            data-token={box.token}
            className={isHighlight ? styles.highlightBox : styles.labelBox}
            x={box.rect.left}
            y={box.rect.top}
            width={box.rect.width}
            height={box.rect.height}
            fill={color}
            fillOpacity={showLabels ? 0.3 : 0}
            stroke={color}
            strokeWidth={2}
            vectorEffect="non-scaling-stroke"
          >
            <title>{`${box.token} — ${box.family}`}</title>
          </rect>
        );
      })}
    </svg>
  );
};

interface LegendProps {
  labelFile: ShowcaseLabelFile;
}

const Legend: React.FC<LegendProps> = ({ labelFile }) => {
  const families = useMemo(() => familiesIn(labelFile), [labelFile]);
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

const AugmentationShowcase: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.15,
    triggerOnce: true,
    fallbackInView: true,
  });

  const [activeId, setActiveId] = useState<string>("base");
  const [showLabels, setShowLabels] = useState(false);
  const [visitedIds, setVisitedIds] = useState<readonly string[]>([]);
  const [labelFiles, setLabelFiles] = useState<
    Record<string, ShowcaseLabelFile>
  >({});
  const viewportRef = useRef<HTMLDivElement>(null);

  const activeVariant = useMemo(
    () =>
      showcaseVariants.find((v) => v.id === activeId) ?? showcaseVariants[0],
    [activeId],
  );
  const activeLabels = labelFiles[activeVariant.id];

  // Prefetch every variant's labels.json once the figure scrolls into view;
  // they are small and make the label toggle + highlights instant.
  useEffect(() => {
    if (!inView) {
      return;
    }
    let cancelled = false;
    showcaseVariants.forEach((variant) => {
      fetch(labelsSrc(variant))
        .then((res) => (res.ok ? res.json() : null))
        .then((data: ShowcaseLabelFile | null) => {
          if (!cancelled && data) {
            setLabelFiles((prev) =>
              prev[variant.id] ? prev : { ...prev, [variant.id]: data },
            );
          }
        })
        .catch(() => {
          // Overlay is progressive enhancement; the renders still show.
        });
    });
    return () => {
      cancelled = true;
    };
  }, [inView]);

  const highlightIndices = useMemo(() => {
    if (!activeLabels || activeVariant.operation === "base_real_receipt") {
      return [];
    }
    return findHighlightIndices(activeLabels, {
      operation: activeVariant.operation,
      itemWords: activeVariant.itemWords,
      newTotal: activeVariant.newTotal,
    });
  }, [activeLabels, activeVariant]);

  // Bring the first changed region into view when an operation is applied.
  useEffect(() => {
    const viewport = viewportRef.current;
    if (
      !viewport ||
      highlightIndices.length === 0 ||
      !activeLabels ||
      typeof viewport.scrollTo !== "function"
    ) {
      return;
    }
    const targetBox = pickScrollTarget(
      buildLabelBoxes(activeLabels),
      highlightIndices,
      activeVariant.itemWords,
    );
    if (!targetBox) {
      return;
    }
    const target =
      (targetBox.rect.top / 100) * viewport.scrollHeight -
      viewport.clientHeight / 2;
    viewport.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
  }, [highlightIndices, activeLabels, activeVariant.itemWords]);

  const handleSelect = useCallback((variant: ShowcaseVariant) => {
    setActiveId(variant.id);
    if (variant.operation !== "base_real_receipt") {
      setVisitedIds((prev) =>
        prev.includes(variant.id) ? prev : [...prev, variant.id],
      );
    }
  }, []);

  const generatedSoFar = visitedIds.length;

  return (
    <div
      ref={ref}
      id="augmentation-showcase"
      data-testid="augmentation-showcase"
      className={styles.container}
    >
      <header className={styles.header}>
        <span className={styles.eyebrow}>Synthetic augmentation</span>
        <h3 className={styles.headline}>
          Real receipts in. Labeled training examples out.
        </h3>
        <p className={styles.counter} data-testid="generated-counter">
          Labeled training examples generated: {generatedSoFar} /{" "}
          {generatedVariantCount}
        </p>
      </header>

      <div className={styles.stage}>
        <div
          ref={viewportRef}
          className={styles.viewport}
          data-testid="receipt-viewport"
        >
          <div
            key={activeVariant.id}
            className={styles.receiptFrame}
            style={{
              aspectRatio: `${activeVariant.width} / ${activeVariant.height}`,
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={renderSrc(activeVariant)}
              alt={`${OPERATION_NAMES[activeVariant.operation]} — ${activeVariant.caption}`}
              width={activeVariant.width}
              height={activeVariant.height}
              loading="lazy"
              className={styles.receiptImage}
            />
            {activeLabels ? (
              <LabelOverlay
                labelFile={activeLabels}
                showLabels={showLabels}
                highlightIndices={highlightIndices}
              />
            ) : null}
          </div>
        </div>

        <aside className={styles.panel}>
          <OperationControls activeId={activeId} onSelect={handleSelect} />

          <button
            type="button"
            className={styles.labelsToggle}
            aria-pressed={showLabels}
            onClick={() => setShowLabels((v) => !v)}
          >
            {showLabels ? "Hide labels" : "Reveal labels"}
          </button>

          <div className={styles.captionCard}>
            <span className={styles.operationName}>
              {OPERATION_NAMES[activeVariant.operation]}
            </span>
            <p className={styles.caption}>{activeVariant.caption}</p>
            {activeVariant.oldTotal && activeVariant.newTotal ? (
              <p className={styles.totalDelta}>
                <span className={styles.oldTotal}>
                  ${activeVariant.oldTotal}
                </span>{" "}
                → <strong>${activeVariant.newTotal}</strong>
              </p>
            ) : null}
            {activeLabels?.metadata?.base_receipt_key ? (
              <p
                className={styles.provenance}
                data-testid="provenance"
              >
                Generated from real receipt{" "}
                <code>
                  {activeLabels.metadata.base_receipt_key.slice(0, 8)}…
                  {activeLabels.metadata.base_receipt_key.split("#")[1] ?? ""}
                </code>
              </p>
            ) : null}
            <dl className={styles.stats}>
              <div className={styles.stat}>
                <dt>Tokens</dt>
                <dd>{activeVariant.tokens}</dd>
              </div>
              <div className={styles.stat}>
                <dt>Labeled</dt>
                <dd>{activeVariant.labeledTokens}</dd>
              </div>
            </dl>
          </div>

          {showLabels && activeLabels ? (
            <Legend labelFile={activeLabels} />
          ) : null}

          <p className={styles.footnote}>
            Every variant is generated from labeled positions, so its
            ground-truth labels come for free.
          </p>
        </aside>
      </div>
    </div>
  );
};

export default AugmentationShowcase;
