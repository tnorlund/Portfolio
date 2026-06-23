import React, { useCallback, useEffect, useState, useRef } from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  DatasetMetrics,
  TrainingMetricsEpoch,
  TrainingSynthesisSummary,
} from "../../../../types/api";
import styles from "./TrainingMetricsAnimation.module.css";
import {
  ConfusionMatrix,
  TopConfusionPairs,
} from "./ConfusionViz";
import {
  EpochSparkline,
  TIMELINE_AUTOPLAY_MAX_STEP_MS,
  TIMELINE_AUTOPLAY_MIN_STEP_MS,
  TIMELINE_AUTOPLAY_TOTAL_MS,
} from "./EpochSparkline";
import {
  BarLegend,
  DatasetStats,
  F1Gauge,
  PerLabelBars,
} from "./LabelStats";
import { TrainingMetricsSkeleton } from "./TrainingMetricsSkeleton";

// Main Component
const TrainingMetricsAnimation: React.FC = () => {
  const { ref: lazyRef, inView: nearViewport } = useInView({
    triggerOnce: true,
    rootMargin: "200px",
    fallbackInView: true,
  });
  const { ref: animRef, inView } = useInView({
    threshold: 0.3,
    triggerOnce: true,
    fallbackInView: true,
  });
  const setRefs = useCallback(
    (node: HTMLDivElement | null) => {
      lazyRef(node);
      animRef(node);
    },
    [lazyRef, animRef],
  );
  const [epochs, setEpochs] = useState<TrainingMetricsEpoch[]>([]);
  const [datasetMetrics, setDatasetMetrics] = useState<DatasetMetrics | undefined>();
  const [synthesis, setSynthesis] = useState<TrainingSynthesisSummary | null>(
    null
  );
  const [currentEpochIndex, setCurrentEpochIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [showBestLabel, setShowBestLabel] = useState(false);
  const hasStartedAnimation = useRef(false);
  const hasFetchedRef = useRef(false);

  // Fetch data only when near viewport - defers work until section is close
  useEffect(() => {
    if (!nearViewport || hasFetchedRef.current) return;
    hasFetchedRef.current = true;

    api
      .fetchFeaturedTrainingMetrics()
      .then((data) => {
        setEpochs(data.epochs);
        setDatasetMetrics(data.dataset_metrics);
        setSynthesis(data.synthesis ?? null);
        setIsLoading(false);
      })
      .catch((err) => {
        console.error("Failed to fetch training metrics:", err);
        setIsLoading(false);
      });
  }, [nearViewport]);

  // Autoplay animation when in view and data is loaded
  useEffect(() => {
    if (!inView || hasStartedAnimation.current || epochs.length === 0 || isLoading) {
      return;
    }

    hasStartedAnimation.current = true;
    setShowBestLabel(false);

    // Find the best epoch index
    const bestIndex = epochs.findIndex((e) => e.is_best);

    // Start from epoch 0, animate through ALL epochs, then land on best.
    // Step interval adapts to epoch count so the whole playback stays
    // bounded — at ~60+ epochs, 1200ms/step would take 75s, which is too
    // long for a marquee animation.
    const stepMs = Math.max(
      TIMELINE_AUTOPLAY_MIN_STEP_MS,
      Math.min(
        TIMELINE_AUTOPLAY_MAX_STEP_MS,
        Math.round(TIMELINE_AUTOPLAY_TOTAL_MS / Math.max(1, epochs.length))
      )
    );
    let currentIndex = 0;
    setCurrentEpochIndex(0);

    const interval = setInterval(() => {
      currentIndex++;

      if (currentIndex >= epochs.length) {
        clearInterval(interval);
        // After showing all epochs, jump to best and show label
        if (bestIndex !== -1) {
          setTimeout(() => {
            setCurrentEpochIndex(bestIndex);
            setShowBestLabel(true);
          }, 500);
        }
        return;
      }

      setCurrentEpochIndex(currentIndex);
    }, stepMs);

    return () => clearInterval(interval);
  }, [inView, epochs, isLoading]);

  const currentEpoch = epochs[currentEpochIndex];

  if (!nearViewport || isLoading) {
    return (
      <div ref={setRefs} className={styles.container}>
        <TrainingMetricsSkeleton />
      </div>
    );
  }

  if (!currentEpoch) {
    return (
      <div ref={setRefs} className={styles.container}>
        <TrainingMetricsSkeleton />
      </div>
    );
  }

  const handleSelectEpoch = (index: number) => {
    setCurrentEpochIndex(index);
  };

  return (
    <div ref={setRefs} className={styles.container}>
      <DatasetStats datasetMetrics={datasetMetrics} />
      <EpochSparkline
        epochs={epochs}
        currentIndex={currentEpochIndex}
        onSelectEpoch={handleSelectEpoch}
        showBestLabel={showBestLabel}
        synthesis={synthesis}
      />

      <div className={styles.leftPanel}>
        <F1Gauge value={currentEpoch.metrics.val_f1} />
        <PerLabelBars perLabel={currentEpoch.per_label} />
        <BarLegend />
      </div>

      <div className={styles.rightPanel}>
        {currentEpoch.confusion_matrix && (
          <>
            <ConfusionMatrix
              labels={currentEpoch.confusion_matrix.labels}
              matrix={currentEpoch.confusion_matrix.matrix}
            />
            <TopConfusionPairs
              labels={currentEpoch.confusion_matrix.labels}
              matrix={currentEpoch.confusion_matrix.matrix}
            />
          </>
        )}
      </div>
    </div>
  );
};

export default TrainingMetricsAnimation;
