import React, { useEffect, useState, useMemo, useRef } from "react";
import { animated, useSprings } from "@react-spring/web";
import useOptimizedInView from "../../../../hooks/useOptimizedInView";
import type { PrecisionRecallDartboardProps } from "./types";
import { DARTBOARD_SCENARIOS } from "./dartPositions";
import DartboardSVG from "./DartboardSVG";

/**
 * A 2x2 grid of dartboards visualizing precision/recall concepts in ML.
 *
 * Layout:
 *                  High Recall    Low Recall
 * High Precision   [clustered     [few darts,
 *                   bullseye]      clustered]
 * Low Precision    [many darts,   [few darts,
 *                   scattered]     scattered]
 */
const PrecisionRecallDartboard: React.FC<PrecisionRecallDartboardProps> = ({
  staggerDelay = 300,
  animationDuration = 600,
  showTitles = false,
  showLabels = true,
  dartSpreadDuration = 400,
  dartPauseDuration = 300,
}) => {
  const [ref, inView] = useOptimizedInView({
    threshold: 0.3,
    triggerOnce: false, // Re-animate when coming back into view
  });
  const [mounted, setMounted] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  const timeoutIds = useRef<NodeJS.Timeout[]>([]);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Responsive dartboard size
  const dartboardSize = useMemo(() => {
    if (typeof window === "undefined") return 160;
    const width = window.innerWidth;
    if (width <= 480) return 130;
    if (width <= 768) return 150;
    return 170;
  }, []);

  // Animation springs for each dartboard
  const [springs, api] = useSprings(
    4,
    () => ({
      opacity: 0,
      transform: "scale(0.9)",
      config: { tension: 120, friction: 14 },
    }),
    []
  );

  // Clear all pending timeouts
  const clearAllTimeouts = () => {
    timeoutIds.current.forEach((id) => clearTimeout(id));
    timeoutIds.current = [];
  };

  // Trigger animations when in view
  useEffect(() => {
    if (!mounted) return;

    if (inView) {
      // Clear any pending timeouts from previous animations
      clearAllTimeouts();

      // Staggered fade-in
      DARTBOARD_SCENARIOS.forEach((_, index) => {
        const id = setTimeout(() => {
          api.start((i) => {
            if (i === index) {
              return {
                opacity: 1,
                transform: "scale(1)",
                config: { tension: 120, friction: 14 },
              };
            }
            return false;
          });
        }, index * staggerDelay);
        timeoutIds.current.push(id);
      });
    } else {
      // Clear pending timeouts when going out of view
      clearAllTimeouts();

      // Reset when out of view
      api.start(() => ({
        opacity: 0,
        transform: "scale(0.9)",
        immediate: true,
      }));
      setResetKey((k) => k + 1);
    }

    // Cleanup on unmount
    return () => clearAllTimeouts();
  }, [inView, mounted, staggerDelay, api]);

  if (!mounted) {
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          minHeight: dartboardSize * 2 + 100,
          alignItems: "center",
        }}
      >
        Loading...
      </div>
    );
  }

  const gap = 16;
  const gridWidth = dartboardSize * 2 + gap;

  return (
    <div
      ref={ref}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        width: "100%",
        padding: "1rem",
      }}
    >
      {/* Recall labels (top) */}
      {showLabels && (
        <div style={{ display: "flex", justifyContent: "center", marginBottom: "0.5rem" }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: `repeat(2, ${dartboardSize}px)`,
              gap: `0 ${gap}px`,
            }}
          >
            <div
              style={{
                textAlign: "center",
                fontWeight: "bold",
                fontSize: "0.875rem",
                color: "var(--text-color)",
              }}
            >
              Low Recall
            </div>
            <div
              style={{
                textAlign: "center",
                fontWeight: "bold",
                fontSize: "0.875rem",
                color: "var(--text-color)",
              }}
            >
              High Recall
            </div>
          </div>
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "center" }}>
        {/* Precision labels (left) - using grid to match dartboard rows */}
        {showLabels && (
          <div
            style={{
              display: "grid",
              gridTemplateRows: "1fr 1fr",
              marginRight: 4,
              width: 24,
              gap: `${gap}px`,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <div
                style={{
                  writingMode: "vertical-rl",
                  textOrientation: "mixed",
                  transform: "rotate(180deg)",
                  fontWeight: "bold",
                  fontSize: "0.875rem",
                  color: "var(--text-color)",
                }}
              >
                Low Precision
              </div>
            </div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <div
                style={{
                  writingMode: "vertical-rl",
                  textOrientation: "mixed",
                  transform: "rotate(180deg)",
                  fontWeight: "bold",
                  fontSize: "0.875rem",
                  color: "var(--text-color)",
                }}
              >
                High Precision
              </div>
            </div>
          </div>
        )}

        {/* 2x2 Grid of dartboards */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: `repeat(2, ${dartboardSize}px)`,
            gridTemplateRows: `repeat(2, 1fr)`,
            gap: `${gap}px`,
          }}
        >
          {DARTBOARD_SCENARIOS.map((scenario, index) => (
            <animated.div
              key={`${scenario.id}-${resetKey}`}
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                ...springs[index],
              }}
            >
              <DartboardSVG
                size={dartboardSize}
                darts={scenario.darts}
                animateDarts={inView}
                dartAnimationDelay={
                  // Wait for all dartboards to appear, then sequence darts
                  (DARTBOARD_SCENARIOS.length * staggerDelay + animationDuration) +
                  (index * (dartSpreadDuration + dartPauseDuration))
                }
                dartSpreadDuration={dartSpreadDuration}
              />
              {showTitles && (
                <div
                  style={{
                    fontSize: "0.7rem",
                    textAlign: "center",
                    marginTop: "0.25rem",
                    color: "var(--text-color)",
                    opacity: 0.75,
                    lineHeight: 1.2,
                  }}
                >
                  {scenario.title}
                </div>
              )}
            </animated.div>
          ))}
        </div>

        {/* Right spacer to balance precision labels */}
        {showLabels && <div style={{ width: 24, marginLeft: 4 }} />}
      </div>
    </div>
  );
};

export default PrecisionRecallDartboard;
