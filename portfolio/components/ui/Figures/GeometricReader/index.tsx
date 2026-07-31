import { animated, useSpring } from "@react-spring/web";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  DecodedReceiptLineItem,
  LineItemDecodeReceipt,
  LineItemDecodeResponse,
  LineItemDecodeSection,
} from "../../../../types/api";
import {
  getBestImageUrl,
  getJpegFallbackUrl,
  usePreloadReceiptImages,
} from "../../../../utils/imageFormat";
import { LabelBoxOverlay, OverlayBox } from "../labelBoxOverlay";
import sharedStyles from "../labelBoxOverlay.module.css";
import { LABEL_COLORS } from "../labelStyles";
import { FlyingReceipt } from "../ReceiptFlow/FlyingReceipt";
import {
  DEFAULT_LAYOUT_VARS,
  ReceiptFlowLoadingShell,
} from "../ReceiptFlow/ReceiptFlowLoadingShell";
import { ReceiptFlowShell } from "../ReceiptFlow/ReceiptFlowShell";
import {
  getQueuePosition,
  getReceiptMotionScale,
  getVisibleQueueIndices,
} from "../ReceiptFlow/receiptFlowUtils";
import { ImageFormatSupport } from "../ReceiptFlow/types";
import { useFlyingReceipt } from "../ReceiptFlow/useFlyingReceipt";
import { useImageFormatSupport } from "../ReceiptFlow/useImageFormatSupport";
import {
  boundsForLineIds,
  findPriceCarrierLineId,
  getReceiptProof,
  orderSections,
} from "./geometry";
import styles from "./GeometricReader.module.css";

type Phase = "sections" | "decode" | "prove";

interface PlaybackState {
  phase: Phase;
  sectionCount: number;
  itemCount: number;
  isTransitioning: boolean;
}

const SECTION_STEP_MS = 650;
const ITEM_STEP_MS = 760;
const PROVE_DURATION_MS = 1900;
const TRANSITION_DURATION_MS = 600;
const QUEUE_REFETCH_THRESHOLD = 7;
const MAX_EMPTY_FETCHES = 3;
const BATCH_SIZE = 10;

const SECTION_STYLES: Record<string, { label: string; color: string }> = {
  STOREFRONT: {
    label: "Storefront",
    color: "color-mix(in srgb, var(--color-blue) 42%, #8794a5)",
  },
  HEADER: {
    label: "Header",
    color: "color-mix(in srgb, var(--color-blue) 42%, #8794a5)",
  },
  ADDRESS: {
    label: "Address",
    color: "color-mix(in srgb, var(--color-blue) 34%, #8794a5)",
  },
  ITEMS: { label: "Items", color: "var(--color-yellow)" },
  ITEMS_VALUE: { label: "Item values", color: "var(--color-yellow)" },
  ITEMS_DESCRIPTION: {
    label: "Item descriptions",
    color: "var(--color-yellow)",
  },
  SECTION_HEADER: { label: "Section", color: "var(--color-yellow)" },
  TOTAL_LINE: { label: "Total line", color: "var(--color-green)" },
  SUMMARY: { label: "Summary", color: "var(--color-green)" },
  PAYMENT: { label: "Payment", color: "var(--color-purple)" },
  FOOTER: {
    label: "Footer",
    color: "color-mix(in srgb, var(--text-color) 48%, transparent)",
  },
  SURVEY: {
    label: "Survey",
    color: "color-mix(in srgb, var(--text-color) 48%, transparent)",
  },
  BARCODE: {
    label: "Barcode",
    color: "color-mix(in srgb, var(--text-color) 48%, transparent)",
  },
};

const sectionStyle = (sectionType: string) =>
  SECTION_STYLES[sectionType] ?? {
    label: sectionType.toLowerCase().replaceAll("_", " "),
    color: "var(--color-blue)",
  };

const makeBox = (top: number, left: number, width: number, height: number) => ({
  x: left,
  y: 1 - top - height,
  width,
  height,
});

const FALLBACK_IMAGES = [
  {
    image_id: "0fca7cfd-183e-4109-87a9-2b7b7b94e82d",
    receipt_id: 2,
    width: 830,
    height: 2827,
    cdn_s3_key: "assets/0fca7cfd-183e-4109-87a9-2b7b7b94e82d_RECEIPT_00002.jpg",
    cdn_webp_s3_key:
      "assets/0fca7cfd-183e-4109-87a9-2b7b7b94e82d_RECEIPT_00002.webp",
    cdn_medium_s3_key:
      "assets/0fca7cfd-183e-4109-87a9-2b7b7b94e82d_RECEIPT_00002.jpg",
  },
  {
    image_id: "c914012e-3fc3-4ba2-b314-fa7d423acac8",
    receipt_id: 2,
    width: 871,
    height: 2914,
    cdn_s3_key: "assets/c914012e-3fc3-4ba2-b314-fa7d423acac8_RECEIPT_00002.jpg",
    cdn_webp_s3_key:
      "assets/c914012e-3fc3-4ba2-b314-fa7d423acac8_RECEIPT_00002.webp",
    cdn_medium_s3_key:
      "assets/c914012e-3fc3-4ba2-b314-fa7d423acac8_RECEIPT_00002.jpg",
  },
  {
    image_id: "00ded398-af6f-4a49-86f7-c79ccb554e48",
    receipt_id: 2,
    width: 792,
    height: 2575,
    cdn_s3_key: "assets/00ded398-af6f-4a49-86f7-c79ccb554e48_RECEIPT_00002.jpg",
    cdn_webp_s3_key:
      "assets/00ded398-af6f-4a49-86f7-c79ccb554e48_RECEIPT_00002.webp",
    cdn_medium_s3_key:
      "assets/00ded398-af6f-4a49-86f7-c79ccb554e48_RECEIPT_00002.jpg",
  },
] as const;

const FALLBACK_LINES = [
  [0, "SPROUTS FARMERS MARKET", 0.06, 0.22, 0.56, 0.025],
  [1, "123 MAIN STREET", 0.12, 0.25, 0.5, 0.019],
  [2, "ORGANIC BANANAS", 0.35, 0.14, 0.46, 0.021],
  [3, "1.31 @ 1.29", 0.39, 0.22, 0.38, 0.019],
  [4, "$1.69", 0.39, 0.73, 0.14, 0.019],
  [5, "ALMOND MILK", 0.46, 0.14, 0.42, 0.021],
  [6, "$4.99", 0.46, 0.73, 0.14, 0.019],
  [7, "SUBTOTAL", 0.75, 0.52, 0.22, 0.022],
  [8, "$6.68", 0.75, 0.75, 0.14, 0.022],
  [9, "VISA 4242", 0.84, 0.18, 0.3, 0.019],
  [10, "THANK YOU", 0.92, 0.32, 0.28, 0.019],
] as const;

const FALLBACK_SECTIONS: LineItemDecodeSection[] = [
  { section_type: "HEADER", line_ids: [0] },
  { section_type: "ADDRESS", line_ids: [1] },
  { section_type: "ITEMS", line_ids: [2, 3, 4, 5, 6] },
  { section_type: "SUMMARY", line_ids: [7, 8] },
  { section_type: "PAYMENT", line_ids: [9] },
  { section_type: "FOOTER", line_ids: [10] },
];

const fallbackReceipt = (
  image: (typeof FALLBACK_IMAGES)[number],
  index: number,
): LineItemDecodeReceipt => {
  const reconciliation = (["match", "mismatch", "near"] as const)[index];
  const printedSubtotal = [6.68, 7.18, 6.7][index];
  return {
    image_id: image.image_id,
    receipt_id: image.receipt_id,
    merchant_name: "Sprouts Farmers Market",
    image: { ...image },
    lines: FALLBACK_LINES.map(([line_id, text, top, left, width, height]) => ({
      line_id,
      text,
      bounding_box: makeBox(top, left, width, height),
    })),
    sections: FALLBACK_SECTIONS,
    line_items: [
      {
        name: "Organic bananas",
        price: "1.69",
        quantity: 1.31,
        unit_price: 1.29,
        is_discount: false,
        line_ids: [2, 3, 4],
        reconciliation_status: reconciliation,
      },
      {
        name: "Almond milk",
        price: "4.99",
        quantity: null,
        unit_price: null,
        is_discount: false,
        line_ids: [5, 6],
        reconciliation_status: reconciliation,
      },
    ],
    printed_subtotal: printedSubtotal,
  };
};

const FALLBACK_RESPONSE: LineItemDecodeResponse = {
  receipts: FALLBACK_IMAGES.map(fallbackReceipt),
  batch_size: FALLBACK_IMAGES.length,
  candidate_count: FALLBACK_IMAGES.length,
  fetched_at: "2026-07-31T00:00:00Z",
};

const usePrefersReducedMotion = (): boolean => {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(query.matches);
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return reduced;
};

interface ReceiptQueueProps {
  receipts: LineItemDecodeReceipt[];
  currentIndex: number;
  formatSupport: ImageFormatSupport | null;
  isTransitioning: boolean;
  isPoolExhausted: boolean;
  shouldAnimate: boolean;
}

const ReceiptQueue: React.FC<ReceiptQueueProps> = ({
  receipts,
  currentIndex,
  formatSupport,
  isTransitioning,
  isPoolExhausted,
  shouldAnimate,
}) => {
  const maxVisible = 6;
  const visibleReceipts = useMemo(
    () =>
      getVisibleQueueIndices(
        receipts.length,
        currentIndex,
        maxVisible,
        isPoolExhausted,
      ).map((index) => receipts[index]),
    [receipts, currentIndex, isPoolExhausted],
  );

  if (!formatSupport) return <div className={styles.receiptQueue} />;

  return (
    <div className={styles.receiptQueue} data-rf-queue>
      {visibleReceipts.map((receipt, index) => {
        const receiptKey = `${receipt.image_id}-${receipt.receipt_id}`;
        const { rotation, leftOffset } = getQueuePosition(receiptKey);
        const adjustedIndex = isTransitioning ? index - 1 : index;
        const isFlying = isTransitioning && index === 0;
        const imageUrl = getBestImageUrl(
          receipt.image,
          formatSupport,
          "thumbnail",
        );
        return (
          <div
            key={`${receiptKey}-queue-${index}`}
            className={styles.queuedReceipt}
            data-rf-card-id={receiptKey}
            style={{
              top: `${Math.max(0, adjustedIndex) * 20}px`,
              left: `${10 + leftOffset}px`,
              opacity: isFlying ? 0 : shouldAnimate ? 1 : 0,
              transform: `rotate(${rotation}deg) translateY(${shouldAnimate ? 0 : -50}px)`,
              transitionDelay: `${index * 50}ms`,
              zIndex: maxVisible - index,
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={imageUrl}
              alt={`Queued receipt ${index + 1}`}
              width={100}
              height={150}
              loading="lazy"
              decoding="async"
              onError={(event) => {
                const fallback = getJpegFallbackUrl(receipt.image);
                if (event.currentTarget.src !== fallback) {
                  event.currentTarget.src = fallback;
                }
              }}
            />
          </div>
        );
      })}
    </div>
  );
};

interface ActiveReceiptProps {
  receipt: LineItemDecodeReceipt;
  sections: LineItemDecodeSection[];
  sectionCount: number;
  itemCount: number;
  phase: Phase;
  formatSupport: ImageFormatSupport | null;
}

const ActiveReceipt: React.FC<ActiveReceiptProps> = ({
  receipt,
  sections,
  sectionCount,
  itemCount,
  phase,
  formatSupport,
}) => {
  const imageUrl = useMemo(
    () =>
      formatSupport ? getBestImageUrl(receipt.image, formatSupport) : null,
    [receipt.image, formatSupport],
  );
  const { width, height } = receipt.image;
  const revealedSections = sections.slice(0, sectionCount);
  const visibleItems = receipt.line_items.slice(0, itemCount);

  const itemBoxes = useMemo<OverlayBox[]>(
    () =>
      visibleItems.flatMap((item, index) => {
        const bounds = boundsForLineIds(receipt.lines, item.line_ids);
        if (!bounds) return [];
        return [
          {
            key: `item-${index}`,
            x: bounds.x * width,
            y: bounds.y * height,
            width: bounds.width * width,
            height: bounds.height * height,
            color: item.is_discount
              ? LABEL_COLORS.DISCOUNT
              : LABEL_COLORS.LINE_TOTAL,
            testId: `item-block-${index}`,
          },
        ];
      }),
    [visibleItems, receipt.lines, width, height],
  );

  const activeItem = phase === "decode" ? visibleItems.at(-1) : undefined;
  const priceCarrier = activeItem
    ? findPriceCarrierLineId(activeItem, receipt.lines)
    : null;
  const priceBounds =
    priceCarrier === null
      ? null
      : boundsForLineIds(receipt.lines, [priceCarrier]);

  if (!imageUrl) return <div className={styles.receiptLoading}>Loading…</div>;

  return (
    <div className={styles.activeReceipt}>
      <div className={styles.receiptImageWrapper}>
        <div
          className={`${styles.receiptImageInner} ${sharedStyles.receiptCard}`}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt={`${receipt.merchant_name ?? "Merchant"} receipt`}
            width={width}
            height={height}
            className={styles.receiptImage}
            onError={(event) => {
              const fallback = getJpegFallbackUrl(receipt.image);
              if (event.currentTarget.src !== fallback) {
                event.currentTarget.src = fallback;
              }
            }}
          />

          <svg
            className={styles.svgOverlay}
            viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            {revealedSections.map((section, index) => {
              const bounds = boundsForLineIds(receipt.lines, section.line_ids);
              if (!bounds) return null;
              const visual = sectionStyle(section.section_type);
              const x = bounds.x * width;
              const y = bounds.y * height;
              const zoneWidth = bounds.width * width;
              const zoneHeight = bounds.height * height;
              const labelWidth = Math.min(
                zoneWidth,
                visual.label.length * 17 + 30,
              );
              return (
                <g
                  key={`${section.section_type}-${index}`}
                  className={
                    index === sectionCount - 1 && phase === "sections"
                      ? styles.activeZone
                      : styles.zone
                  }
                  data-testid={`section-zone-${section.section_type}`}
                >
                  <rect
                    x={x}
                    y={y}
                    width={zoneWidth}
                    height={zoneHeight}
                    fill={visual.color}
                    fillOpacity={0.22}
                    stroke={visual.color}
                    strokeWidth={2}
                  />
                  <rect
                    x={x}
                    y={Math.max(0, y - 25)}
                    width={labelWidth}
                    height={25}
                    fill={visual.color}
                    fillOpacity={0.92}
                  />
                  <text
                    x={x + 9}
                    y={Math.max(17, y - 7)}
                    className={styles.zoneCaption}
                  >
                    {visual.label.toUpperCase()}
                  </text>
                </g>
              );
            })}
          </svg>

          {itemBoxes.length > 0 ? (
            <LabelBoxOverlay
              width={width}
              height={height}
              boxes={itemBoxes}
              className={styles.svgOverlay}
            />
          ) : null}

          {priceBounds ? (
            <svg
              className={styles.svgOverlay}
              viewBox={`0 0 ${width} ${height}`}
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <rect
                x={priceBounds.x * width}
                y={priceBounds.y * height}
                width={priceBounds.width * width}
                height={priceBounds.height * height}
                fill={LABEL_COLORS.LINE_TOTAL}
                fillOpacity={0.42}
                stroke={LABEL_COLORS.LINE_TOTAL}
                strokeWidth={5}
                className={styles.priceCarrier}
                data-testid="price-carrier"
              />
            </svg>
          ) : null}
        </div>
      </div>
    </div>
  );
};

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

const formatCurrency = (value: number): string =>
  currencyFormatter.format(value);

const quantityLabel = (item: DecodedReceiptLineItem): string | null => {
  if (item.quantity === null) return null;
  const quantity = Number.isInteger(item.quantity)
    ? item.quantity.toFixed(0)
    : item.quantity.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  return item.unit_price === null
    ? `×${quantity}`
    : `${quantity} × ${formatCurrency(item.unit_price)}`;
};

interface LedgerPanelProps {
  receipt: LineItemDecodeReceipt;
  sections: LineItemDecodeSection[];
  phase: Phase;
  sectionCount: number;
  itemCount: number;
  reducedMotion: boolean;
}

const LedgerPanel: React.FC<LedgerPanelProps> = ({
  receipt,
  sections,
  phase,
  sectionCount,
  itemCount,
  reducedMotion,
}) => {
  const decodedTotal = receipt.line_items
    .slice(0, itemCount)
    .reduce((sum, item) => sum + (Number.parseFloat(item.price) || 0), 0);
  const totalSpring = useSpring({
    amount: decodedTotal,
    immediate: reducedMotion || phase !== "decode",
    config: { tension: 210, friction: 24 },
  });
  const proof = useMemo(() => getReceiptProof(receipt), [receipt]);
  const proofVisible = phase === "prove";
  const statusClass =
    proof.status === "match"
      ? styles.match
      : proof.status === "near"
        ? styles.near
        : proof.status === "mismatch"
          ? styles.mismatch
          : styles.noBaseline;

  return (
    <div className={styles.ledgerPanel} data-testid="geometric-reader-ledger">
      <div className={styles.panelHeader}>
        <span className={styles.eyebrow}>Deterministic decode</span>
        <strong>{receipt.merchant_name ?? "Unknown merchant"}</strong>
      </div>

      <div className={styles.phaseRail} aria-label={`Pipeline phase: ${phase}`}>
        {(["sections", "decode", "prove"] as const).map((value, index) => (
          <React.Fragment key={value}>
            {index > 0 ? <span className={styles.phaseLine} /> : null}
            <span
              className={`${styles.phaseStep} ${phase === value ? styles.activePhase : ""}`}
            >
              {value}
            </span>
          </React.Fragment>
        ))}
      </div>

      <div
        className={`${styles.sectionList} ${phase !== "sections" ? styles.compactSections : ""}`}
      >
        {sections.map((section, index) => {
          const visual = sectionStyle(section.section_type);
          const revealed = index < sectionCount;
          return (
            <div
              key={`${section.section_type}-${index}`}
              className={`${styles.sectionRow} ${revealed ? styles.revealed : ""}`}
              aria-hidden={!revealed}
            >
              <span
                className={styles.sectionSwatch}
                style={{ background: visual.color }}
              />
              <span>{visual.label}</span>
              <span className={styles.lineCount}>
                {section.line_ids.length}L
              </span>
            </div>
          );
        })}
      </div>

      <div className={styles.ledger} aria-label="Decoded line items">
        <div className={styles.ledgerHeading}>
          <span>Item</span>
          <span>Price</span>
        </div>
        {receipt.line_items.map((item, index) => {
          const revealed = index < itemCount;
          const quantity = quantityLabel(item);
          return (
            <div
              key={`${item.name}-${index}`}
              className={`${styles.ledgerRow} ${revealed ? styles.revealed : ""} ${item.is_discount ? styles.discount : ""}`}
              aria-hidden={!revealed}
            >
              <span className={styles.itemName}>
                {item.name || "Unnamed item"}
                {quantity ? (
                  <small className={styles.quantityChip}>{quantity}</small>
                ) : null}
              </span>
              <span className={styles.itemPrice}>
                {formatCurrency(Number.parseFloat(item.price) || 0)}
              </span>
            </div>
          );
        })}
      </div>

      <div className={styles.runningTotal}>
        <span>Decoded sum</span>
        <animated.span data-testid="decoded-total">
          {totalSpring.amount.to((amount) => formatCurrency(amount))}
        </animated.span>
      </div>

      <div
        className={`${styles.proof} ${proofVisible ? styles.proofVisible : ""}`}
        aria-live="polite"
        aria-hidden={!proofVisible}
      >
        <div className={styles.proofEquation}>
          <span>{formatCurrency(proof.decodedTotal)}</span>
          <span className={styles.proofOperator}>↔</span>
          <span>
            {proof.printedSubtotal === null
              ? "—"
              : formatCurrency(proof.printedSubtotal)}
          </span>
        </div>
        <div className={`${styles.proofBadge} ${statusClass}`}>
          {proof.status === "match"
            ? "reconciled · exact"
            : proof.status === "near"
              ? `near · Δ ${formatCurrency(Math.abs(proof.delta ?? 0))}`
              : proof.status === "mismatch"
                ? `mismatch · Δ ${formatCurrency(Math.abs(proof.delta ?? 0))}`
                : proof.printedSubtotal === null
                  ? "printed subtotal unavailable"
                  : "reconciliation unavailable"}
        </div>
        {proof.status === "near" || proof.status === "mismatch" ? (
          <span className={styles.reocrNote}>re-OCR queued</span>
        ) : null}
      </div>
    </div>
  );
};

const emptyPlayback: PlaybackState = {
  phase: "sections",
  sectionCount: 0,
  itemCount: 0,
  isTransitioning: false,
};

const samePlayback = (left: PlaybackState, right: PlaybackState) =>
  left.phase === right.phase &&
  left.sectionCount === right.sectionCount &&
  left.itemCount === right.itemCount &&
  left.isTransitioning === right.isTransitioning;

interface GeometricReaderInnerProps {
  observerRef: (node?: Element | null) => void;
  inView: boolean;
  receipts: LineItemDecodeReceipt[];
  formatSupport: ImageFormatSupport | null;
  isPoolExhausted: boolean;
  onFetchMore: () => void;
}

const GeometricReaderInner: React.FC<GeometricReaderInnerProps> = ({
  observerRef,
  inView,
  receipts,
  formatSupport,
  isPoolExhausted,
  onFetchMore,
}) => {
  const reducedMotion = usePrefersReducedMotion();
  const [currentIndex, setCurrentIndex] = useState(0);
  const [playback, setPlayback] = useState<PlaybackState>(emptyPlayback);
  const [queueVisible, setQueueVisible] = useState(false);
  const animationRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);
  const receiptsRef = useRef(receipts);
  const isPoolExhaustedRef = useRef(isPoolExhausted);
  receiptsRef.current = receipts;
  isPoolExhaustedRef.current = isPoolExhausted;

  useEffect(() => {
    if (inView && receipts.length > 0) setQueueVisible(true);
  }, [inView, receipts.length]);

  const remainingReceipts = receipts.length - currentIndex;
  useEffect(() => {
    if (remainingReceipts < QUEUE_REFETCH_THRESHOLD && !isPoolExhausted) {
      onFetchMore();
    }
  }, [remainingReceipts, isPoolExhausted, onFetchMore]);

  useEffect(() => {
    if (!reducedMotion) return;
    const receipt = receipts[currentIndex];
    if (!receipt) return;
    setPlayback({
      phase: "prove",
      sectionCount: receipt.sections.length,
      itemCount: receipt.line_items.length,
      isTransitioning: false,
    });
  }, [reducedMotion, receipts, currentIndex]);

  useEffect(() => {
    if (
      !inView ||
      receipts.length === 0 ||
      reducedMotion ||
      isAnimatingRef.current
    ) {
      return;
    }
    isAnimatingRef.current = true;
    let receiptIndex = currentIndex;
    let startTime = performance.now();

    const publish = (next: PlaybackState) => {
      setPlayback((previous) =>
        samePlayback(previous, next) ? previous : next,
      );
    };

    const animate = (now: number) => {
      const currentReceipts = receiptsRef.current;
      const receipt = currentReceipts[receiptIndex];
      if (!receipt) {
        animationRef.current = requestAnimationFrame(animate);
        return;
      }

      const sections = orderSections(receipt.sections);
      const motionScale = getReceiptMotionScale();
      const sectionDuration =
        Math.max(1, sections.length) * SECTION_STEP_MS * motionScale;
      const decodeDuration =
        Math.max(1, receipt.line_items.length) * ITEM_STEP_MS * motionScale;
      const proveDuration = PROVE_DURATION_MS * motionScale;
      const transitionDuration = TRANSITION_DURATION_MS * motionScale;
      const elapsed = now - startTime;

      if (elapsed < sectionDuration) {
        publish({
          phase: "sections",
          sectionCount:
            sections.length === 0
              ? 0
              : Math.min(
                  sections.length,
                  Math.floor(elapsed / (SECTION_STEP_MS * motionScale)) + 1,
                ),
          itemCount: 0,
          isTransitioning: false,
        });
      } else if (elapsed < sectionDuration + decodeDuration) {
        const decodeElapsed = elapsed - sectionDuration;
        publish({
          phase: "decode",
          sectionCount: sections.length,
          itemCount:
            receipt.line_items.length === 0
              ? 0
              : Math.min(
                  receipt.line_items.length,
                  Math.floor(decodeElapsed / (ITEM_STEP_MS * motionScale)) + 1,
                ),
          isTransitioning: false,
        });
      } else if (elapsed < sectionDuration + decodeDuration + proveDuration) {
        publish({
          phase: "prove",
          sectionCount: sections.length,
          itemCount: receipt.line_items.length,
          isTransitioning: false,
        });
      } else if (
        elapsed <
        sectionDuration + decodeDuration + proveDuration + transitionDuration
      ) {
        publish({
          phase: "prove",
          sectionCount: sections.length,
          itemCount: receipt.line_items.length,
          isTransitioning: true,
        });
      } else {
        let nextIndex = receiptIndex + 1;
        if (nextIndex >= currentReceipts.length) {
          if (!isPoolExhaustedRef.current) {
            animationRef.current = requestAnimationFrame(animate);
            return;
          }
          nextIndex = 0;
        }
        receiptIndex = nextIndex;
        setCurrentIndex(nextIndex);
        publish(emptyPlayback);
        startTime = now;
      }
      animationRef.current = requestAnimationFrame(animate);
    };

    animationRef.current = requestAnimationFrame(animate);
    return () => {
      if (animationRef.current !== null)
        cancelAnimationFrame(animationRef.current);
      isAnimatingRef.current = false;
    };
    // The rAF loop owns receiptIndex after startup and reads changing data via refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inView, receipts.length > 0, reducedMotion]);

  const getNextReceipt = useCallback(
    (items: LineItemDecodeReceipt[], index: number) => {
      const next = index + 1;
      return isPoolExhausted
        ? items[next % items.length]
        : (items[next] ?? null);
    },
    [isPoolExhausted],
  );
  const { flyingItem, showFlying } = useFlyingReceipt(
    playback.isTransitioning,
    receipts,
    currentIndex,
    getNextReceipt,
  );

  const flyingElement = useMemo(() => {
    if (!showFlying || !flyingItem || !formatSupport) return null;
    const imageUrl = getBestImageUrl(flyingItem.image, formatSupport);
    const aspectRatio = flyingItem.image.width / flyingItem.image.height;
    let displayHeight = Math.min(500, flyingItem.image.height);
    let displayWidth = displayHeight * aspectRatio;
    if (displayWidth > 350) {
      displayWidth = 350;
      displayHeight = displayWidth / aspectRatio;
    }
    return (
      <FlyingReceipt
        imageUrl={imageUrl}
        displayWidth={displayWidth}
        displayHeight={displayHeight}
        receiptId={`${flyingItem.image_id}-${flyingItem.receipt_id}`}
      />
    );
  }, [showFlying, flyingItem, formatSupport]);

  const currentReceipt = receipts[currentIndex];
  const currentSections = useMemo(
    () => orderSections(currentReceipt.sections),
    [currentReceipt.sections],
  );
  const nextReceipt = isPoolExhausted
    ? receipts[(currentIndex + 1) % receipts.length]
    : receipts[currentIndex + 1];
  const nextSections = nextReceipt ? orderSections(nextReceipt.sections) : [];
  const motionScale = getReceiptMotionScale();
  const layoutVars = useMemo(
    () =>
      ({
        ...DEFAULT_LAYOUT_VARS,
        "--rf-align-items": "center",
        "--rf-legend-width": "240px",
        "--rf-legend-stage-height": "500px",
        "--rf-mobile-legend-height": "28rem",
        "--rf-motion-scale": motionScale,
      }) as React.CSSProperties,
    [motionScale],
  );

  return (
    <div
      ref={observerRef}
      className={styles.container}
      data-testid="geometric-reader"
    >
      <ReceiptFlowShell
        layoutVars={layoutVars}
        isTransitioning={playback.isTransitioning}
        stabilizeLegend
        queue={
          <ReceiptQueue
            receipts={receipts}
            currentIndex={currentIndex}
            formatSupport={formatSupport}
            isTransitioning={playback.isTransitioning}
            isPoolExhausted={isPoolExhausted}
            shouldAnimate={queueVisible}
          />
        }
        center={
          <ActiveReceipt
            receipt={currentReceipt}
            sections={currentSections}
            sectionCount={playback.sectionCount}
            itemCount={playback.itemCount}
            phase={playback.phase}
            formatSupport={formatSupport}
          />
        }
        flying={flyingElement}
        next={
          playback.isTransitioning && nextReceipt ? (
            <ActiveReceipt
              receipt={nextReceipt}
              sections={nextSections}
              sectionCount={0}
              itemCount={0}
              phase="sections"
              formatSupport={formatSupport}
            />
          ) : null
        }
        legend={
          <LedgerPanel
            receipt={currentReceipt}
            sections={currentSections}
            phase={playback.phase}
            sectionCount={playback.sectionCount}
            itemCount={playback.itemCount}
            reducedMotion={reducedMotion}
          />
        }
        nextLegend={
          playback.isTransitioning && nextReceipt ? (
            <LedgerPanel
              receipt={nextReceipt}
              sections={nextSections}
              phase="sections"
              sectionCount={0}
              itemCount={0}
              reducedMotion={reducedMotion}
            />
          ) : null
        }
      />
    </div>
  );
};

const GeometricReader: React.FC = () => {
  const { ref: lazyRef, inView: nearViewport } = useInView({
    triggerOnce: true,
    rootMargin: "200px",
  });
  const { ref: animationRef, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
  });
  const setRefs = useCallback(
    (node?: Element | null) => {
      lazyRef(node);
      animationRef(node);
    },
    [lazyRef, animationRef],
  );
  const [receipts, setReceipts] = useState<LineItemDecodeReceipt[]>([]);
  const [initialLoading, setInitialLoading] = useState(true);
  const [isPoolExhausted, setIsPoolExhausted] = useState(false);
  const formatSupport = useImageFormatSupport();
  const isFetchingRef = useRef(false);
  const didInitialFetchRef = useRef(false);
  const emptyFetchCountRef = useRef(0);
  const seenReceiptIds = useRef(new Set<string>());

  usePreloadReceiptImages(
    receipts.map((receipt) => receipt.image),
    formatSupport,
  );

  const appendResponse = useCallback((response: LineItemDecodeResponse) => {
    const newReceipts = response.receipts.filter((receipt) => {
      const key = `${receipt.image_id}-${receipt.receipt_id}`;
      if (seenReceiptIds.current.has(key)) return false;
      seenReceiptIds.current.add(key);
      return true;
    });
    if (newReceipts.length > 0) {
      setReceipts((current) => [...current, ...newReceipts]);
      emptyFetchCountRef.current = 0;
    } else {
      emptyFetchCountRef.current += 1;
      if (emptyFetchCountRef.current >= MAX_EMPTY_FETCHES) {
        setIsPoolExhausted(true);
      }
    }
  }, []);

  const fetchMore = useCallback(async () => {
    if (isFetchingRef.current || isPoolExhausted) return;
    isFetchingRef.current = true;
    try {
      appendResponse(await api.fetchLineItemDecode(BATCH_SIZE));
    } catch (error) {
      console.error("Failed to fetch more geometric-reader receipts:", error);
      setIsPoolExhausted(true);
    } finally {
      isFetchingRef.current = false;
    }
  }, [appendResponse, isPoolExhausted]);

  useEffect(() => {
    if (!nearViewport || didInitialFetchRef.current) return;
    didInitialFetchRef.current = true;
    const load = async () => {
      try {
        const response = await api.fetchLineItemDecode(BATCH_SIZE);
        if (response.receipts.length === 0)
          throw new Error("No decoded receipts");
        appendResponse(response);
      } catch (error) {
        console.error("Using geometric-reader fallback data:", error);
        appendResponse(FALLBACK_RESPONSE);
        setIsPoolExhausted(true);
      } finally {
        setInitialLoading(false);
      }
    };
    load();
  }, [nearViewport, appendResponse]);

  if (!nearViewport || initialLoading) {
    return (
      <div ref={setRefs} className={styles.container}>
        <ReceiptFlowLoadingShell
          variant="layoutlm"
          layoutVars={
            {
              ...DEFAULT_LAYOUT_VARS,
              "--rf-align-items": "center",
            } as React.CSSProperties
          }
        />
      </div>
    );
  }

  if (receipts.length === 0) {
    return (
      <div ref={setRefs} className={styles.container}>
        <ReceiptFlowLoadingShell
          variant="layoutlm"
          message="No line-item decode data available"
        />
      </div>
    );
  }

  return (
    <GeometricReaderInner
      observerRef={setRefs}
      inView={inView}
      receipts={receipts}
      formatSupport={formatSupport}
      isPoolExhausted={isPoolExhausted}
      onFetchMore={fetchMore}
    />
  );
};

export default GeometricReader;
