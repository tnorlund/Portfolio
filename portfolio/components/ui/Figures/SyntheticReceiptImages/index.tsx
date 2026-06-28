import React, { useEffect, useMemo, useState } from "react";
import { useInView } from "react-intersection-observer";
import { FlyingReceipt } from "../ReceiptFlow/FlyingReceipt";
import {
  DEFAULT_LAYOUT_VARS,
  ReceiptFlowLoadingShell,
} from "../ReceiptFlow/ReceiptFlowLoadingShell";
import { ReceiptFlowShell } from "../ReceiptFlow/ReceiptFlowShell";
import {
  getQueuePosition,
  getVisibleQueueIndices,
} from "../ReceiptFlow/receiptFlowUtils";
import { useFlyingReceipt } from "../ReceiptFlow/useFlyingReceipt";
import {
  SyntheticReceiptImage,
  syntheticReceiptImages,
} from "./syntheticReceiptCache";
import styles from "./SyntheticReceiptImages.module.css";

const HOLD_DURATION_MS = 1600;
const TRANSITION_DURATION_MS = 650;

const LAYOUT_VARS = {
  ...DEFAULT_LAYOUT_VARS,
  "--rf-align-items": "center",
  "--rf-center-height": "520px",
  "--rf-center-max-width": "360px",
  "--rf-mobile-center-height": "420px",
  "--rf-mobile-center-height-sm": "360px",
  "--rf-legend-width": "260px",
  "--rf-legend-height": "390px",
  "--rf-legend-stage-height": "390px",
  "--rf-mobile-legend-height": "16rem",
} as React.CSSProperties;

const formatScore = (score?: number) =>
  score === undefined ? "n/a" : score.toFixed(3);

const totalDelta = (sample: SyntheticReceiptImage) => {
  if (!sample.oldGrandTotal || !sample.newGrandTotal) {
    return sample.labelTarget ?? "label target";
  }
  return `$${sample.oldGrandTotal} -> $${sample.newGrandTotal}`;
};

interface ReceiptQueueProps {
  samples: SyntheticReceiptImage[];
  currentIndex: number;
  isTransitioning: boolean;
}

const ReceiptQueue: React.FC<ReceiptQueueProps> = ({
  samples,
  currentIndex,
  isTransitioning,
}) => {
  const visibleIndices = useMemo(() => {
    const maxVisible = Math.min(4, Math.max(0, samples.length - 1));
    return getVisibleQueueIndices(samples.length, currentIndex, maxVisible, true);
  }, [samples.length, currentIndex]);

  return (
    <div className={styles.receiptQueue} data-rf-queue>
      {visibleIndices.map((sampleIndex, idx) => {
        const sample = samples[sampleIndex];
        const { rotation, leftOffset } = getQueuePosition(sample.id);
        const adjustedIdx = isTransitioning ? idx - 1 : idx;
        const top = Math.max(0, adjustedIdx) * 24;
        const isFlying = isTransitioning && idx === 0;

        return (
          <div
            key={`${sample.id}-${idx}`}
            className={`${styles.queuedReceipt} ${isFlying ? styles.flyingOut : ""}`}
            data-rf-card-id={sample.id}
            style={{
              top,
              left: 10 + leftOffset,
              transform: `rotate(${rotation}deg)`,
              opacity: isFlying ? 0 : 1,
              zIndex: visibleIndices.length - idx,
              transition:
                "top 0.4s ease, left 0.4s ease, opacity 0.25s ease-out, transform 0.4s ease",
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={sample.imageSrc}
              alt={`${sample.title} queue receipt`}
              width={sample.width}
              height={sample.height}
              style={{ width: "100%", height: "auto", display: "block" }}
            />
          </div>
        );
      })}
    </div>
  );
};

interface ReceiptViewerProps {
  sample: SyntheticReceiptImage;
}

const ReceiptViewer: React.FC<ReceiptViewerProps> = ({ sample }) => (
  <div className={styles.receiptStage}>
    <div className={styles.receiptImageWrapper}>
      <div className={styles.receiptImageInner}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={sample.imageSrc}
          alt={`Synthetic ${sample.title} receipt`}
          width={sample.width}
          height={sample.height}
          className={styles.receiptImage}
        />
      </div>
    </div>
  </div>
);

interface CacheCardProps {
  sample: SyntheticReceiptImage;
}

const CacheCard: React.FC<CacheCardProps> = ({ sample }) => (
  <aside className={styles.cacheCard} aria-label={`${sample.title} cache details`}>
    <div className={styles.cardHeader}>
      <span className={styles.eyebrow}>Synthetic cache</span>
      <h3 className={styles.title}>{sample.title}</h3>
      <span className={styles.operation}>{sample.operation}</span>
    </div>

    <div className={styles.metricGrid}>
      <div className={styles.metric}>
        <span className={styles.metricLabel}>Tokens</span>
        <span className={styles.metricValue}>{sample.tokenCount}</span>
      </div>
      <div className={styles.metric}>
        <span className={styles.metricLabel}>Lines</span>
        <span className={styles.metricValue}>{sample.lineCount}</span>
      </div>
      <div className={styles.metric}>
        <span className={styles.metricLabel}>Structure</span>
        <span className={styles.metricValue}>{formatScore(sample.structureScore)}</span>
      </div>
      <div className={styles.metric}>
        <span className={styles.metricLabel}>
          {sample.oldGrandTotal ? "Total" : "Target"}
        </span>
        <span className={styles.metricValue}>{totalDelta(sample)}</span>
      </div>
    </div>

    {sample.expectedEffect ? (
      <p className={styles.note}>{sample.expectedEffect}</p>
    ) : null}

    {sample.reviewFocus?.[0] ? (
      <div className={styles.reviewFocus}>
        <strong>Review focus</strong>
        <span>{sample.reviewFocus[0]}</span>
      </div>
    ) : null}

    <div className={styles.reason}>
      <strong>Train-only guard</strong>
      {sample.trainOnlyReason}
    </div>
  </aside>
);

const getNextSample = (
  samples: SyntheticReceiptImage[],
  idx: number,
): SyntheticReceiptImage | null => samples[(idx + 1) % samples.length] ?? null;

const SyntheticReceiptImages: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
    fallbackInView: true,
  });

  const samples = syntheticReceiptImages;
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isTransitioning, setIsTransitioning] = useState(false);

  useEffect(() => {
    if (!inView || samples.length <= 1) {
      return;
    }

    let transitionTimer: ReturnType<typeof setTimeout> | undefined;
    const holdTimer = setTimeout(() => {
      setIsTransitioning(true);
      transitionTimer = setTimeout(() => {
        setCurrentIndex((idx) => (idx + 1) % samples.length);
        setIsTransitioning(false);
      }, TRANSITION_DURATION_MS);
    }, HOLD_DURATION_MS);

    return () => {
      clearTimeout(holdTimer);
      if (transitionTimer) {
        clearTimeout(transitionTimer);
      }
    };
  }, [inView, currentIndex, samples.length]);

  const currentSample = samples[currentIndex];
  const nextSample = samples[(currentIndex + 1) % samples.length];

  const { flyingItem, showFlying } = useFlyingReceipt(
    isTransitioning,
    samples,
    currentIndex,
    getNextSample,
  );

  const flyingElement = useMemo(() => {
    if (!showFlying || !flyingItem) {
      return null;
    }
    const aspectRatio = flyingItem.width / flyingItem.height;
    let displayHeight = Math.min(500, flyingItem.height);
    let displayWidth = displayHeight * aspectRatio;
    if (displayWidth > 350) {
      displayWidth = 350;
      displayHeight = displayWidth / aspectRatio;
    }

    return (
      <FlyingReceipt
        key={`flying-${flyingItem.id}`}
        imageUrl={flyingItem.imageSrc}
        displayWidth={displayWidth}
        displayHeight={displayHeight}
        receiptId={flyingItem.id}
      />
    );
  }, [showFlying, flyingItem]);

  if (!currentSample) {
    return (
      <div
        ref={ref}
        id="synthetic-receipt-images"
        data-testid="synthetic-receipt-images"
        className={styles.container}
      >
        <ReceiptFlowLoadingShell
          layoutVars={LAYOUT_VARS}
          variant="synthetic"
          message="No synthetic receipt image cache available"
        />
      </div>
    );
  }

  return (
    <div
      ref={ref}
      id="synthetic-receipt-images"
      data-testid="synthetic-receipt-images"
      className={styles.container}
    >
      <ReceiptFlowShell
        layoutVars={LAYOUT_VARS}
        isTransitioning={isTransitioning}
        stabilizeLegend
        queue={
          <ReceiptQueue
            samples={samples}
            currentIndex={currentIndex}
            isTransitioning={isTransitioning}
          />
        }
        center={<ReceiptViewer sample={currentSample} />}
        flying={flyingElement}
        next={
          isTransitioning && nextSample ? (
            <ReceiptViewer sample={nextSample} />
          ) : null
        }
        legend={<CacheCard sample={currentSample} />}
        nextLegend={
          isTransitioning && nextSample ? <CacheCard sample={nextSample} /> : null
        }
      />
    </div>
  );
};

export default SyntheticReceiptImages;
