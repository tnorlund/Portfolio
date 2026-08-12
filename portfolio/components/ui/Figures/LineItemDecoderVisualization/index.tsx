import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useInView } from "react-intersection-observer";
import {
  LineItemDemoBand,
  LineItemDemoLine,
  LineItemDemoReceipt,
  LineItemDemoResponse,
  LineItemDemoWord,
} from "../../../../types/api";
import {
  getBestImageUrl,
  getJpegFallbackUrl,
  usePreloadReceiptImages,
} from "../../../../utils/imageFormat";
import { ReceiptFlowShell } from "../ReceiptFlow/ReceiptFlowShell";
import {
  DEFAULT_LAYOUT_VARS,
  ReceiptFlowLoadingShell,
} from "../ReceiptFlow/ReceiptFlowLoadingShell";
import {
  getQueuePosition,
  getReceiptMotionScale,
  getVisibleQueueIndices,
} from "../ReceiptFlow/receiptFlowUtils";
import { ImageFormatSupport } from "../ReceiptFlow/types";
import { useImageFormatSupport } from "../ReceiptFlow/useImageFormatSupport";
import { FlyingReceipt } from "../ReceiptFlow/FlyingReceipt";
import { useFlyingReceipt } from "../ReceiptFlow/useFlyingReceipt";
import { LABEL_COLORS } from "../labelStyles";
import sharedStyles from "../labelBoxOverlay.module.css";
import styles from "./LineItemDecoderVisualization.module.css";
import {
  DecoderFrame,
  DecoderStage,
  DecoderStageKey,
  HOLD_DURATION,
  TRANSITION_DURATION,
  buildStagePlan,
  computeFrame,
  displayItems,
  donorBands,
  rejectedBands,
  revealCount,
  stageStarted,
} from "./decoderTimeline";

const DATA_URL = "/line-item-demo/receipts.json";

const LAYOUT_VARS = {
  ...DEFAULT_LAYOUT_VARS,
  "--rf-align-items": "center",
} as React.CSSProperties;

const STATUS_META: Record<
  string,
  { label: string; icon: string; color: string }
> = {
  match: { label: "Match", icon: "✓", color: "var(--color-green)" },
  near: { label: "Near", icon: "≈", color: "var(--color-yellow)" },
  mismatch: { label: "Mismatch", icon: "✗", color: "var(--color-red)" },
  "no-baseline": {
    label: "No printed total",
    icon: "—",
    color: "var(--text-color)",
  },
};

// Legend rows mirror the LayoutLM entity legend: a fixed list whose dots
// double as the color key for the receipt overlay. Rows light up as the
// decoder passes each stage on the current receipt.
const LEGEND_STAGES: { key: DecoderStageKey; label: string; color: string }[] =
  [
    {
      key: "rows",
      label: "Group lines → embeddings",
      color: "var(--color-cyan)",
    },
    {
      key: "sections",
      label: "Assign sections",
      color: "var(--color-yellow)",
    },
    { key: "zone", label: "Find the items zone", color: "var(--color-orange)" },
    {
      key: "bands",
      label: "Group into rows",
      color: "var(--text-color)",
    },
    { key: "guards", label: "Reject non-products", color: "var(--color-red)" },
    {
      key: "pair",
      label: "Pair names + prices",
      color: LABEL_COLORS.PRODUCT_NAME,
    },
    { key: "qty", label: "Verify quantities", color: LABEL_COLORS.QUANTITY },
    { key: "reconcile", label: "Check the sum", color: "var(--color-green)" },
  ];

const fmtMoney = (v: number | null | undefined): string =>
  v == null ? "—" : `$${Math.abs(v).toFixed(2)}`;

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

const wordKey = (lineId: number, wordId: number) => `${lineId}_${wordId}`;

interface PxBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Normalized bottom-left-origin bbox → pixel top-left-origin box. */
const toPx = (
  bbox: { x: number; y: number; width: number; height: number },
  w: number,
  h: number
): PxBox => ({
  x: bbox.x * w,
  y: (1 - bbox.y - bbox.height) * h,
  width: bbox.width * w,
  height: bbox.height * h,
});

const extendBox = (acc: PxBox | null, box: PxBox): PxBox => {
  if (!acc) return { ...box };
  const x = Math.min(acc.x, box.x);
  const y = Math.min(acc.y, box.y);
  return {
    x,
    y,
    width: Math.max(acc.x + acc.width, box.x + box.width) - x,
    height: Math.max(acc.y + acc.height, box.y + box.height) - y,
  };
};

// ─── Receipt Queue (Left Column) ────────────────────────────────────────────

interface ReceiptQueueProps {
  receipts: LineItemDemoReceipt[];
  currentIndex: number;
  formatSupport: ImageFormatSupport | null;
  isTransitioning: boolean;
}

const ReceiptQueue: React.FC<ReceiptQueueProps> = ({
  receipts,
  currentIndex,
  formatSupport,
  isTransitioning,
}) => {
  const maxVisible = 6;
  const STACK_GAP = 20;

  const visibleReceipts = useMemo(() => {
    if (receipts.length === 0) return [];
    return getVisibleQueueIndices(
      receipts.length,
      currentIndex,
      maxVisible,
      true
    ).map((idx) => receipts[idx]);
  }, [receipts, currentIndex]);

  if (!formatSupport || visibleReceipts.length === 0) {
    return <div className={styles.receiptQueue} />;
  }

  return (
    <div className={styles.receiptQueue} data-rf-queue>
      {visibleReceipts.map((receipt, idx) => {
        const imageUrl = getBestImageUrl(
          receipt.image,
          formatSupport,
          "thumbnail"
        );
        const receiptKey = `${receipt.image_id}-${receipt.receipt_id}`;
        const { rotation, leftOffset } = getQueuePosition(receiptKey);
        const adjustedIdx = isTransitioning ? idx - 1 : idx;
        const stackOffset = Math.max(0, adjustedIdx) * STACK_GAP;
        const isFlying = isTransitioning && idx === 0;

        return (
          <div
            key={`${receiptKey}-queue-${idx}`}
            className={`${styles.queuedReceipt} ${isFlying ? styles.flyingOut : ""}`}
            data-rf-card-id={receiptKey}
            style={{
              top: `${stackOffset}px`,
              left: `${10 + leftOffset}px`,
              transform: `rotate(${rotation}deg)`,
              zIndex: maxVisible - idx,
            }}
          >
            {imageUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={imageUrl}
                alt={`Queued receipt ${idx + 1}`}
                width={100}
                height={150}
                loading="lazy"
                decoding="async"
                style={{ width: "100%", height: "auto", display: "block" }}
                onError={(e) => {
                  const fallback = getJpegFallbackUrl(receipt.image);
                  if (e.currentTarget.src !== fallback) {
                    e.currentTarget.src = fallback;
                  }
                }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
};

// ─── Active Receipt Viewer (Center Column) ──────────────────────────────────

interface ActiveReceiptViewerProps {
  receipt: LineItemDemoReceipt;
  plan: DecoderStage[];
  frame: DecoderFrame;
  formatSupport: ImageFormatSupport | null;
}

const ActiveReceiptViewer: React.FC<ActiveReceiptViewerProps> = ({
  receipt,
  plan,
  frame,
  formatSupport,
}) => {
  const { width: w, height: h } = receipt.image;

  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(receipt.image, formatSupport);
  }, [receipt.image, formatSupport]);

  const wordMap = useMemo(() => {
    const map = new Map<string, LineItemDemoWord>();
    for (const word of receipt.words) {
      map.set(wordKey(word.line_id, word.word_id), word);
    }
    return map;
  }, [receipt.words]);

  const lineMap = useMemo(() => {
    const map = new Map<number, LineItemDemoLine>();
    for (const line of receipt.lines ?? []) {
      map.set(line.line_id, line);
    }
    return map;
  }, [receipt.lines]);

  const boxFor = useCallback(
    (ref: [number, number] | null): PxBox | null => {
      if (!ref) return null;
      const word = wordMap.get(wordKey(ref[0], ref[1]));
      return word ? toPx(word.bbox, w, h) : null;
    },
    [wordMap, w, h]
  );

  const bandBox = useCallback(
    (band: LineItemDemoBand): PxBox | null => {
      let acc: PxBox | null = null;
      for (const ref of band.word_refs) {
        const box = boxFor(ref);
        if (box) acc = extendBox(acc, box);
      }
      if (!acc) return null;
      const PAD = 3;
      return {
        x: acc.x - PAD,
        y: acc.y - PAD,
        width: acc.width + PAD * 2,
        height: acc.height + PAD * 2,
      };
    },
    [boxFor]
  );

  const normBox = useCallback(
    (bbox: { x: number; y: number; width: number; height: number }, pad = 2): PxBox => {
      const box = toPx(bbox, w, h);
      return {
        x: Math.max(0, box.x - pad),
        y: Math.max(0, box.y - pad),
        width: Math.min(w, box.width + pad * 2),
        height: Math.min(h, box.height + pad * 2),
      };
    },
    [w, h]
  );

  // The items-zone extent, from the zone words.
  const zoneBox = useMemo(() => {
    let acc: PxBox | null = null;
    for (const word of receipt.words) {
      if (!word.in_zone) continue;
      acc = extendBox(acc, toPx(word.bbox, w, h));
    }
    if (!acc) return null;
    const PAD = 6;
    return {
      x: Math.max(0, acc.x - PAD),
      y: Math.max(0, acc.y - PAD),
      width: Math.min(w, acc.width + PAD * 2),
      height: Math.min(h, acc.height + PAD * 2),
    };
  }, [receipt.words, w, h]);

  const rejected = useMemo(() => rejectedBands(receipt), [receipt]);
  const donors = useMemo(() => donorBands(receipt), [receipt]);
  const itemsTopFirst = useMemo(() => displayItems(receipt), [receipt]);
  const qtyItems = useMemo(
    () =>
      itemsTopFirst.filter(
        (i) => i.quantity != null && i.unit_price != null
      ),
    [itemsTopFirst]
  );
  const visualRows = receipt.visual_rows ?? [];
  const sections = receipt.sections ?? [];

  const rowsRevealed = revealCount(visualRows.length, plan, frame, "rows");
  const sectionsRevealed = revealCount(
    sections.length,
    plan,
    frame,
    "sections"
  );
  const zoneVisible = stageStarted(plan, frame, "zone");
  const bandsRevealed = revealCount(
    receipt.bands.length,
    plan,
    frame,
    "bands"
  );
  const rejectsRevealed = revealCount(rejected.length, plan, frame, "guards");
  const itemsRevealed = revealCount(
    itemsTopFirst.length,
    plan,
    frame,
    "pair"
  );
  const qtyRevealed = revealCount(qtyItems.length, plan, frame, "qty");
  const donorsRevealed = revealCount(donors.length, plan, frame, "qty");
  const reconcileProgress =
    frame.stageKey === "reconcile" && frame.phase === "stages"
      ? frame.progress
      : stageStarted(plan, frame, "reconcile")
        ? 1
        : 0;

  // Early structure layers fade once the ITEMS-zone decoder takes over.
  const rowsOpacity = stageStarted(plan, frame, "bands")
    ? 0.12
    : stageStarted(plan, frame, "sections")
      ? 0.45
      : 1;
  const sectionsOpacity = stageStarted(plan, frame, "bands") ? 0.18 : 1;

  const rejectedIds = useMemo(
    () => new Set(rejected.slice(0, rejectsRevealed).map((b) => b.band_id)),
    [rejected, rejectsRevealed]
  );

  // Word boxes for revealed items: name (purple), price (green),
  // discounts teal. Dropped items appear at reconcile and fade.
  const revealedItems = itemsTopFirst.slice(0, itemsRevealed);
  const revealedQty = qtyItems.slice(0, qtyRevealed);
  const revealedDonors = donors.slice(0, donorsRevealed);
  const revealedRows = visualRows.slice(0, rowsRevealed);
  const revealedSections = sections.slice(0, sectionsRevealed);

  if (!imageUrl) {
    return <div className={styles.receiptLoading}>Loading...</div>;
  }

  return (
    <div className={styles.activeReceipt}>
      <div className={styles.receiptImageWrapper}>
        <div
          className={`${styles.receiptImageInner} ${sharedStyles.receiptCard}`}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt={`${receipt.merchant} receipt`}
            width={w}
            height={h}
            className={styles.receiptImage}
            onError={(e) => {
              const fallback = getJpegFallbackUrl(receipt.image);
              if (e.currentTarget.src !== fallback) {
                e.currentTarget.src = fallback;
              }
            }}
          />

          <svg
            className={styles.svgOverlay}
            viewBox={`0 0 ${w} ${h}`}
            preserveAspectRatio="none"
          >
            {/* Stage: sections — tint STOREFRONT / ITEMS / SUMMARY */}
            <g
              className={styles.stageLayer}
              style={{ opacity: sectionsOpacity }}
            >
              {revealedSections.map((section) => {
                const box = normBox(section.bbox, 4);
                return (
                  <g key={`section-${section.section_type}`}>
                    <rect
                      x={box.x}
                      y={box.y}
                      width={box.width}
                      height={box.height}
                      fill={section.color}
                      fillOpacity={0.14}
                      stroke={section.color}
                      strokeWidth={2}
                      strokeDasharray="6,4"
                      opacity={0.9}
                    />
                  </g>
                );
              })}
            </g>

            {/* Stage: zone — dim everything outside the ITEMS section */}
            {zoneBox && (
              <g
                className={styles.stageLayer}
                style={{ opacity: zoneVisible ? 1 : 0 }}
              >
                <rect
                  x={0}
                  y={0}
                  width={w}
                  height={Math.max(0, zoneBox.y)}
                  fill="var(--background-color)"
                  opacity={0.55}
                />
                <rect
                  x={0}
                  y={zoneBox.y + zoneBox.height}
                  width={w}
                  height={Math.max(0, h - zoneBox.y - zoneBox.height)}
                  fill="var(--background-color)"
                  opacity={0.55}
                />
                <rect
                  x={zoneBox.x}
                  y={zoneBox.y}
                  width={zoneBox.width}
                  height={zoneBox.height}
                  fill="none"
                  stroke="var(--color-orange)"
                  strokeWidth={2}
                  strokeDasharray="8,5"
                  opacity={0.8}
                />
              </g>
            )}

            {/* Stage: rows — ReceiptLines grouped into embedding units.
                Multi-line rows show dashed line boxes inside a solid row. */}
            <g className={styles.stageLayer} style={{ opacity: rowsOpacity }}>
              {revealedRows.map((row) => {
                const outer = normBox(row.bbox, 3);
                const multi = row.line_ids.length > 1;
                return (
                  <g key={`row-${row.row_id}`}>
                    {multi &&
                      row.line_ids.map((lineId) => {
                        const line = lineMap.get(lineId);
                        if (!line) return null;
                        const box = normBox(line.bbox, 1);
                        return (
                          <rect
                            key={`rowline-${row.row_id}-${lineId}`}
                            x={box.x}
                            y={box.y}
                            width={box.width}
                            height={box.height}
                            fill="none"
                            stroke="var(--color-cyan)"
                            strokeWidth={1}
                            strokeDasharray="3,2"
                            opacity={0.7}
                          />
                        );
                      })}
                    <rect
                      x={outer.x}
                      y={outer.y}
                      width={outer.width}
                      height={outer.height}
                      rx={2}
                      fill="var(--color-cyan)"
                      fillOpacity={multi ? 0.08 : 0.04}
                      stroke="var(--color-cyan)"
                      strokeWidth={multi ? 2 : 1.25}
                      opacity={0.85}
                    />
                  </g>
                );
              })}
            </g>

            {/* Stage: bands — one thin outline per visual row */}
            {receipt.bands.slice(0, bandsRevealed).map((band) => {
              const box = bandBox(band);
              if (!box) return null;
              const isRejected = rejectedIds.has(band.band_id);
              const isItemBand =
                band.outcome === "item" &&
                itemsRevealed > 0 &&
                revealedItems.some((item) =>
                  band.word_refs.some(
                    (ref) =>
                      item.price_word_id &&
                      ref[0] === item.price_word_id[0] &&
                      ref[1] === item.price_word_id[1]
                  )
                );
              const isDonorBand = revealedDonors.some(
                (d) => d.band_id === band.band_id
              );
              const stroke = isRejected
                ? "var(--color-red)"
                : isDonorBand
                  ? "var(--color-cyan)"
                  : "rgba(var(--text-color-rgb), 0.45)";
              return (
                <g key={`band-${band.band_id}`} className={styles.stageLayer}>
                  <rect
                    x={box.x}
                    y={box.y}
                    width={box.width}
                    height={box.height}
                    rx={2}
                    fill={isRejected ? "var(--color-red)" : "none"}
                    fillOpacity={isRejected ? 0.12 : 0}
                    stroke={stroke}
                    strokeWidth={isRejected || isDonorBand ? 2 : 1.25}
                    style={{
                      opacity: isItemBand ? 0.25 : 1,
                      transition: "opacity 0.4s ease",
                    }}
                  />
                  {isRejected && (
                    <line
                      x1={box.x}
                      y1={box.y + box.height / 2}
                      x2={box.x + box.width}
                      y2={box.y + box.height / 2}
                      stroke="var(--color-red)"
                      strokeWidth={2}
                      opacity={0.7}
                    />
                  )}
                </g>
              );
            })}

            {/* Stage: pair — label-colored word boxes per decoded item */}
            {revealedItems.map((item, idx) => {
              const nameColor = item.is_discount
                ? LABEL_COLORS.DISCOUNT
                : LABEL_COLORS.PRODUCT_NAME;
              const priceColor = item.is_discount
                ? LABEL_COLORS.DISCOUNT
                : LABEL_COLORS.LINE_TOTAL;
              const priceBox = boxFor(item.price_word_id);
              return (
                <g key={`item-${idx}`} className={styles.stageLayer}>
                  {item.name_word_ids.map((ref, i) => {
                    const box = boxFor(ref);
                    if (!box) return null;
                    return (
                      <rect
                        key={`name-${idx}-${i}`}
                        x={box.x}
                        y={box.y}
                        width={box.width}
                        height={box.height}
                        fill={nameColor}
                        fillOpacity={0.3}
                        stroke={nameColor}
                        strokeWidth={2}
                      />
                    );
                  })}
                  {priceBox && (
                    <rect
                      x={priceBox.x}
                      y={priceBox.y}
                      width={priceBox.width}
                      height={priceBox.height}
                      fill={priceColor}
                      fillOpacity={0.3}
                      stroke={priceColor}
                      strokeWidth={2}
                    />
                  )}
                </g>
              );
            })}

            {/* Stage: qty — quantity words on revealed qty items */}
            {revealedQty.map((item, idx) => (
              <g key={`qty-${idx}`} className={styles.stageLayer}>
                {item.qty_word_ids.map((ref, i) => {
                  const box = boxFor(ref);
                  if (!box) return null;
                  return (
                    <rect
                      key={`qtyw-${idx}-${i}`}
                      x={box.x}
                      y={box.y}
                      width={box.width}
                      height={box.height}
                      fill={LABEL_COLORS.QUANTITY}
                      fillOpacity={0.3}
                      stroke={LABEL_COLORS.QUANTITY}
                      strokeWidth={2}
                    />
                  );
                })}
              </g>
            ))}

            {/* Stage: reconcile — highlight the printed figure the sum is
                checked against (punches through the out-of-zone dim) */}
            {reconcileProgress > 0 &&
              (receipt.reconcile.baseline_source === "subtotal"
                ? receipt.printed_word_refs.subtotal
                : receipt.printed_word_refs.grand_total
              ).map((ref, idx) => {
                const box = boxFor(ref);
                if (!box) return null;
                const PAD = 3;
                return (
                  <rect
                    key={`printed-${idx}`}
                    x={box.x - PAD}
                    y={box.y - PAD}
                    width={box.width + PAD * 2}
                    height={box.height + PAD * 2}
                    rx={2}
                    fill="var(--color-green)"
                    fillOpacity={0.3 * reconcileProgress}
                    stroke="var(--color-green)"
                    strokeWidth={2}
                    opacity={reconcileProgress}
                  />
                );
              })}

            {/* Stage: reconcile — summary-figure rows fade out */}
            {reconcileProgress > 0 &&
              receipt.dropped_items.map((item, idx) => {
                const box = boxFor(item.price_word_id);
                if (!box) return null;
                const fade = Math.min(1, reconcileProgress / 0.4);
                return (
                  <rect
                    key={`dropped-${idx}`}
                    x={box.x}
                    y={box.y}
                    width={box.width}
                    height={box.height}
                    fill="var(--color-red)"
                    fillOpacity={0.25 * (1 - fade)}
                    stroke="var(--color-red)"
                    strokeWidth={2}
                    opacity={1 - fade * 0.8}
                  />
                );
              })}
          </svg>
        </div>
      </div>
    </div>
  );
};

// ─── Stage Legend (Right Column) ────────────────────────────────────────────
// Mirrors LayoutLMBatchVisualization's EntityLegend: dot + label rows that
// light up as the decoder works, plus one stat block at the bottom.

interface StageLegendProps {
  receipt: LineItemDemoReceipt;
  plan: DecoderStage[];
  frame: DecoderFrame;
}

const StageLegend: React.FC<StageLegendProps> = ({ receipt, plan, frame }) => {
  const showVerdict =
    stageStarted(plan, frame, "reconcile") &&
    (frame.stageKey !== "reconcile" || frame.progress > 0.5);

  const status = STATUS_META[receipt.reconcile.status] ?? {
    label: receipt.reconcile.status,
    icon: "?",
    color: "var(--text-color)",
  };
  const itemCount = receipt.items.filter((i) => !i.is_discount).length;
  const discountCount = receipt.items.length - itemCount;
  const itemSum =
    receipt.reconcile.item_sum ??
    receipt.items
      .filter((i) => !i.is_discount)
      .reduce((sum, i) => sum + (i.price ?? 0), 0);
  const printedValue =
    receipt.reconcile.status === "no-baseline"
      ? null
      : (receipt.reconcile.baseline ??
        receipt.summary?.grand_total ??
        receipt.summary?.subtotal ??
        null);
  const printedLabel =
    receipt.reconcile.status === "no-baseline"
      ? "no printed total"
      : receipt.reconcile.baseline_source === "subtotal"
        ? "printed subtotal"
        : receipt.reconcile.baseline_source === "grand_total_minus_tax"
          ? receipt.summary?.tax != null
            ? "total − tax"
            : "printed total"
          : "printed total";

  return (
    <div className={styles.stageLegend}>
      {LEGEND_STAGES.map((stage) => {
        const lit = stageStarted(plan, frame, stage.key);
        return (
          <div
            key={stage.key}
            className={`${styles.legendItem} ${lit ? styles.revealed : ""}`}
          >
            <div
              className={styles.legendDot}
              style={{ backgroundColor: stage.color }}
            />
            <span className={styles.legendLabel}>{stage.label}</span>
          </div>
        );
      })}
      <div
        className={styles.verdictBlock}
        style={{ opacity: showVerdict ? 1 : 0.2 }}
      >
        <div className={styles.verdictRow}>
          <span className={styles.verdictLabel}>
            {itemCount} product{itemCount === 1 ? "" : "s"}
            {discountCount > 0 ? ` + ${discountCount} disc.` : ""}
          </span>
          <span className={styles.verdictValue}>{fmtMoney(itemSum)}</span>
        </div>
        <div className={styles.verdictRow}>
          <span className={styles.verdictLabel}>{printedLabel}</span>
          <span className={styles.verdictValue}>
            {fmtMoney(printedValue)}{" "}
            <span style={{ color: status.color }}>{status.icon}</span>
          </span>
        </div>
      </div>
    </div>
  );
};

// ─── Main Component ─────────────────────────────────────────────────────────

const QUANT = 50; // progress quantization steps per stage

export default function LineItemDecoderVisualization() {
  const { ref: lazyRef, inView: nearViewport } = useInView({
    triggerOnce: true,
    rootMargin: "200px",
  });
  const { ref: animRef, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
  });
  const setRefs = useCallback(
    (node?: Element | null) => {
      lazyRef(node);
      animRef(node);
    },
    [lazyRef, animRef]
  );

  const [receipts, setReceipts] = useState<LineItemDemoReceipt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [frame, setFrame] = useState<DecoderFrame>({
    stageIndex: 0,
    stageKey: "rows",
    progress: 0,
    phase: "stages",
  });
  const [isTransitioning, setIsTransitioning] = useState(false);
  const formatSupport = useImageFormatSupport();
  const reducedMotion = usePrefersReducedMotion();

  // Preload the active receipt plus the next transition target — not the
  // whole showcase set — so scrolling near the figure stays cheap.
  const imageFormats = useMemo(() => {
    if (receipts.length === 0) return [];
    const nextIndex = (currentIndex + 1) % receipts.length;
    const idxs =
      nextIndex === currentIndex
        ? [currentIndex]
        : [currentIndex, nextIndex];
    return idxs.map((i) => receipts[i].image);
  }, [receipts, currentIndex]);
  usePreloadReceiptImages(imageFormats, formatSupport);

  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const receiptsRef = useRef(receipts);
  receiptsRef.current = receipts;

  const plans = useMemo(
    () => receipts.map((r) => buildStagePlan(r)),
    [receipts]
  );
  const plansRef = useRef(plans);
  plansRef.current = plans;

  // Fetch the static payload when near the viewport.
  useEffect(() => {
    if (!nearViewport) return;
    let cancelled = false;
    fetch(DATA_URL)
      .then((resp) => {
        if (!resp.ok) throw new Error(`status ${resp.status}`);
        return resp.json();
      })
      .then((data: LineItemDemoResponse) => {
        if (cancelled) return;
        setReceipts(data.receipts ?? []);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load");
      })
      .finally(() => {
        if (!cancelled) setInitialLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [nearViewport]);

  // Animation loop: stepped decoder stages instead of a single scan.
  // Under prefers-reduced-motion, freeze on the fully-resolved last stage.
  const hasReceipts = receipts.length > 0;
  useEffect(() => {
    if (!hasReceipts || !reducedMotion) return;
    const plan = plansRef.current[currentIndex];
    if (!plan?.length) return;
    const last = plan.length - 1;
    setIsTransitioning(false);
    setFrame({
      stageIndex: last,
      stageKey: plan[last].key,
      progress: 1,
      phase: "hold",
    });
  }, [hasReceipts, reducedMotion, currentIndex]);

  useEffect(() => {
    if (!inView || !hasReceipts || reducedMotion) return;
    if (isAnimatingRef.current) return;
    isAnimatingRef.current = true;

    let receiptIndex = currentIndex;
    let startTime = performance.now();
    let wasTransitioning = false;

    const animate = (now: number) => {
      const currentReceipts = receiptsRef.current;
      const currentPlans = plansRef.current;
      if (currentReceipts.length === 0) {
        isAnimatingRef.current = false;
        return;
      }
      const plan = currentPlans[receiptIndex];
      if (!plan) {
        animationRef.current = requestAnimationFrame(animate);
        return;
      }

      const motionScale = getReceiptMotionScale();
      const elapsed = (now - startTime) / motionScale;
      const nextFrame = computeFrame(
        plan,
        elapsed,
        HOLD_DURATION,
        TRANSITION_DURATION
      );

      if (nextFrame.phase === "next") {
        receiptIndex = (receiptIndex + 1) % currentReceipts.length;
        startTime = now;
        wasTransitioning = false;
        setCurrentIndex(receiptIndex);
        setIsTransitioning(false);
        setFrame({
          stageIndex: 0,
          stageKey: currentPlans[receiptIndex]?.[0]?.key ?? "rows",
          progress: 0,
          phase: "stages",
        });
      } else {
        const transitioning = nextFrame.phase === "transition";
        if (transitioning !== wasTransitioning) {
          wasTransitioning = transitioning;
          setIsTransitioning(transitioning);
        }
        // Quantize progress so state updates (and re-renders) are bounded.
        const quantized: DecoderFrame = {
          ...nextFrame,
          progress: Math.round(nextFrame.progress * QUANT) / QUANT,
        };
        setFrame((prev) =>
          prev.stageIndex === quantized.stageIndex &&
          prev.progress === quantized.progress &&
          prev.phase === quantized.phase
            ? prev
            : quantized
        );
      }

      animationRef.current = requestAnimationFrame(animate);
    };

    animationRef.current = requestAnimationFrame(animate);
    return () => {
      if (animationRef.current) cancelAnimationFrame(animationRef.current);
      isAnimatingRef.current = false;
      // Leaving mid-transition must not leave the shell stuck in the
      // flying/next-legend presentation when the loop restarts.
      setIsTransitioning(false);
    };
    // The loop owns receiptIndex after startup (same pattern as
    // LayoutLMBatchVisualization).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inView, hasReceipts, reducedMotion]);

  const getNextReceipt = useCallback(
    (items: LineItemDemoReceipt[], idx: number) =>
      items[(idx + 1) % items.length],
    []
  );

  const { flyingItem, showFlying } = useFlyingReceipt(
    isTransitioning,
    receipts,
    currentIndex,
    getNextReceipt
  );

  const flyingElement = useMemo(() => {
    if (!showFlying || !flyingItem || !formatSupport) return null;
    const fUrl = getBestImageUrl(flyingItem.image, formatSupport);
    if (!fUrl) return null;
    const ar = flyingItem.image.width / flyingItem.image.height;
    let dh = Math.min(500, flyingItem.image.height);
    let dw = dh * ar;
    if (dw > 350) {
      dw = 350;
      dh = dw / ar;
    }
    const key = `${flyingItem.image_id}-${flyingItem.receipt_id}`;
    return (
      <FlyingReceipt
        key={`flying-${key}`}
        imageUrl={fUrl}
        displayWidth={dw}
        displayHeight={dh}
        receiptId={key}
      />
    );
  }, [showFlying, flyingItem, formatSupport]);

  if (!nearViewport || initialLoading) {
    return (
      <div ref={setRefs} className={styles.container}>
        <ReceiptFlowLoadingShell layoutVars={LAYOUT_VARS} variant="financial" />
      </div>
    );
  }

  if (error || receipts.length === 0) {
    return (
      <div ref={setRefs} className={styles.container}>
        <ReceiptFlowLoadingShell
          layoutVars={LAYOUT_VARS}
          variant="financial"
          message={error ? `Error: ${error}` : "No decoder data available"}
          isError={Boolean(error)}
        />
      </div>
    );
  }

  const currentReceipt = receipts[currentIndex];
  const currentPlan = plans[currentIndex];
  const nextIndex = (currentIndex + 1) % receipts.length;
  const nextReceipt = receipts[nextIndex];
  const nextPlan = plans[nextIndex];
  const initialFrame: DecoderFrame = {
    stageIndex: 0,
    stageKey: nextPlan?.[0]?.key ?? "rows",
    progress: 0,
    phase: "stages",
  };

  const motionScale = getReceiptMotionScale();
  const layoutVars = {
    ...LAYOUT_VARS,
    "--rf-motion-scale": motionScale,
  } as React.CSSProperties;

  return (
    <div ref={setRefs} className={styles.container}>
      <ReceiptFlowShell
        layoutVars={layoutVars}
        isTransitioning={isTransitioning}
        queue={
          <ReceiptQueue
            receipts={receipts}
            currentIndex={currentIndex}
            formatSupport={formatSupport}
            isTransitioning={isTransitioning}
          />
        }
        center={
          <ActiveReceiptViewer
            receipt={currentReceipt}
            plan={currentPlan}
            frame={frame}
            formatSupport={formatSupport}
          />
        }
        flying={flyingElement}
        next={
          isTransitioning && nextReceipt ? (
            <ActiveReceiptViewer
              receipt={nextReceipt}
              plan={nextPlan}
              frame={initialFrame}
              formatSupport={formatSupport}
            />
          ) : null
        }
        legend={
          <StageLegend
            receipt={currentReceipt}
            plan={currentPlan}
            frame={frame}
          />
        }
        nextLegend={
          isTransitioning && nextReceipt ? (
            <StageLegend
              receipt={nextReceipt}
              plan={nextPlan}
              frame={initialFrame}
            />
          ) : null
        }
        stabilizeLegend
      />
    </div>
  );
}
