import React, { useEffect, useState, useMemo } from "react";
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
  showTitles = true,
  showLabels = true,
}) => {
  const [ref, inView] = useOptimizedInView({
    threshold: 0.3,
    triggerOnce: false, // Re-animate when coming back into view
  });
  const [mounted, setMounted] = useState(false);
  const [resetKey, setResetKey] = useState(0);

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

  // Trigger animations when in view
  useEffect(() => {
    if (!mounted) return;

    if (inView) {
      // Staggered fade-in
      DARTBOARD_SCENARIOS.forEach((_, index) => {
        setTimeout(() => {
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
      });
    } else {
      // Reset when out of view
      api.start(() => ({
        opacity: 0,
        transform: "scale(0.9)",
        immediate: true,
      }));
      setResetKey((k) => k + 1);
    }
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
      {/* Recall labels (top) - matches bottom flex structure */}
      {showLabels && (
        <div style={{ display: "flex", justifyContent: "center", marginBottom: "0.5rem" }}>
          <div style={{ width: 40, marginRight: 8 }} /> {/* Spacer for precision labels */}
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
              High Recall
            </div>
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
          </div>
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "center" }}>
        {/* Precision labels (left) */}
        {showLabels && (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-around",
              marginRight: 8,
              height: dartboardSize * 2 + gap,
              width: 40,
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
                textAlign: "center",
              }}
            >
              High Precision
            </div>
            <div
              style={{
                writingMode: "vertical-rl",
                textOrientation: "mixed",
                transform: "rotate(180deg)",
                fontWeight: "bold",
                fontSize: "0.875rem",
                color: "var(--text-color)",
                textAlign: "center",
              }}
            >
              Low Precision
            </div>
          </div>
        )}

        {/* 2x2 Grid of dartboards */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: `repeat(2, ${dartboardSize}px)`,
            gridTemplateRows: `repeat(2, auto)`,
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
                dartAnimationDelay={index * staggerDelay + animationDuration}
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
      </div>
    </div>
  );
};

export default PrecisionRecallDartboard;
