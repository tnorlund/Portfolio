import React, { useState, useEffect, useRef } from "react";
import { useInView } from "react-intersection-observer";
import ReceiptView from "./ReceiptView";
import PatternScatterPlot from "./PatternScatterPlot";
import { mockData } from "./mockData";
import styles from "./GeometricAnomalyVisualization.module.css";

type AnimationStage = "idle" | "labeling" | "detecting" | "pattern" | "complete";

const STAGE_LABELS: Record<AnimationStage, string> = {
  idle: "Waiting",
  labeling: "Labeling Words",
  detecting: "Detecting Anomalies",
  pattern: "Analyzing Patterns",
  complete: "Complete",
};

const GeometricAnomalyVisualization: React.FC = () => {
  const { ref, inView } = useInView({
    threshold: 0.3,
    triggerOnce: true,
  });

  const [stage, setStage] = useState<AnimationStage>("idle");
  const [animationProgress, setAnimationProgress] = useState(0);
  const [selectedPatternIndex, setSelectedPatternIndex] = useState(0);
  const [highlightedWordId, setHighlightedWordId] = useState<string | null>(null);
  const hasStartedAnimation = useRef(false);

  // Animation sequence when component comes into view
  useEffect(() => {
    if (!inView || hasStartedAnimation.current) return;

    hasStartedAnimation.current = true;
    setStage("labeling");

    // Stage 1: Labeling words (0-0.5)
    const labelingInterval = setInterval(() => {
      setAnimationProgress((prev) => {
        if (prev >= 0.5) {
          clearInterval(labelingInterval);
          return prev;
        }
        return prev + 0.02;
      });
    }, 50);

    // Stage 2: Detecting anomalies
    const detectingTimeout = setTimeout(() => {
      setStage("detecting");
      const detectingInterval = setInterval(() => {
        setAnimationProgress((prev) => {
          if (prev >= 0.7) {
            clearInterval(detectingInterval);
            return prev;
          }
          return prev + 0.02;
        });
      }, 50);
    }, 1500);

    // Stage 3: Pattern analysis
    const patternTimeout = setTimeout(() => {
      setStage("pattern");
      // Highlight the flagged word
      setHighlightedWordId(mockData.flaggedWord?.wordId || null);

      const patternInterval = setInterval(() => {
        setAnimationProgress((prev) => {
          if (prev >= 1) {
            clearInterval(patternInterval);
            return 1;
          }
          return prev + 0.02;
        });
      }, 50);
    }, 2500);

    // Stage 4: Complete
    const completeTimeout = setTimeout(() => {
      setStage("complete");
    }, 4000);

    return () => {
      clearInterval(labelingInterval);
      clearTimeout(detectingTimeout);
      clearTimeout(patternTimeout);
      clearTimeout(completeTimeout);
    };
  }, [inView]);

  const handleWordClick = (wordId: string) => {
    // Find if this word is the flagged one
    const word = mockData.receipt.words.find((w) => w.id === wordId);
    if (word?.isFlagged) {
      // Find the pattern that matches this word's anomaly
      // For now, select the first pattern (SUBTOTAL -> GRAND_TOTAL)
      setSelectedPatternIndex(0);
      setHighlightedWordId(wordId);
    }
  };

  const currentPattern = mockData.patterns.labelPairs[selectedPatternIndex];

  // Only show flagged word info when the selected pattern matches
  const showFlaggedWord =
    currentPattern.from === "SUBTOTAL" && currentPattern.to === "GRAND_TOTAL";

  return (
    <div ref={ref} className={styles.container}>
      {/* Stage indicator */}
      <div className={styles.stageIndicator}>
        {(["labeling", "detecting", "pattern", "complete"] as AnimationStage[]).map(
          (s, index) => (
            <React.Fragment key={s}>
              {index > 0 && (
                <div
                  className={`${styles.stageConnector} ${
                    stage === s ||
                    (["detecting", "pattern", "complete"].indexOf(stage) > index - 1)
                      ? styles.stageConnectorActive
                      : ""
                  }`}
                />
              )}
              <div
                className={`${styles.stageStep} ${
                  stage === s ? styles.stageStepActive : ""
                } ${
                  ["detecting", "pattern", "complete"].indexOf(stage) >
                  ["labeling", "detecting", "pattern", "complete"].indexOf(s)
                    ? styles.stageStepComplete
                    : ""
                }`}
              >
                <span
                  className={`${styles.stageDot} ${
                    stage === s ? styles.stageDotActive : ""
                  } ${
                    ["detecting", "pattern", "complete"].indexOf(stage) >
                    ["labeling", "detecting", "pattern", "complete"].indexOf(s)
                      ? styles.stageDotComplete
                      : ""
                  }`}
                />
                {STAGE_LABELS[s]}
              </div>
            </React.Fragment>
          )
        )}
      </div>

      {/* Main panels */}
      <div className={styles.panelsContainer}>
        <ReceiptView
          words={mockData.receipt.words}
          animationProgress={Math.min(animationProgress * 2, 1)}
          highlightedWordId={highlightedWordId}
          onWordHover={setHighlightedWordId}
          onWordClick={handleWordClick}
        />

        <PatternScatterPlot
          pattern={currentPattern}
          flaggedWord={showFlaggedWord ? mockData.flaggedWord : null}
          animationProgress={animationProgress}
          allPatterns={mockData.patterns.labelPairs}
          selectedPatternIndex={selectedPatternIndex}
          onSelectPattern={setSelectedPatternIndex}
        />
      </div>
    </div>
  );
};

export default GeometricAnomalyVisualization;
