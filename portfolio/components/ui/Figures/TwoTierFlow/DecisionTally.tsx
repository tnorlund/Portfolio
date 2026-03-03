import React from "react";
import { animated, useSpring } from "@react-spring/web";
import { DecisionIcon, EmptyIcon } from "./DecisionIcon";
import type { RevealedDecision } from "./types";
import styles from "./TwoTierFlow.module.css";

// Tally layout constants
const LEGEND_WIDTH = 280;
const LEGEND_PADDING = 32;
const TALLY_PADDING_LEFT = 24;
const TALLY_ICON_SIZE = 14;
const TALLY_ICON_GAP = 2;
const TALLY_MAX_ROWS = 3;
const TALLY_ROW_HEIGHT = TALLY_ICON_SIZE + TALLY_ICON_GAP + 4;

const TALLY_AVAILABLE_WIDTH =
  LEGEND_WIDTH - LEGEND_PADDING - TALLY_PADDING_LEFT;
const TALLY_ICONS_PER_ROW = Math.floor(
  TALLY_AVAILABLE_WIDTH / (TALLY_ICON_SIZE + TALLY_ICON_GAP),
);
const TALLY_MAX_VISIBLE = TALLY_ICONS_PER_ROW * TALLY_MAX_ROWS;

const calculateTallyHeight = (iconCount: number): number => {
  if (iconCount <= 0) return 0;
  const rows = Math.min(
    Math.ceil(iconCount / TALLY_ICONS_PER_ROW),
    TALLY_MAX_ROWS,
  );
  return rows * TALLY_ROW_HEIGHT;
};

interface DecisionTallyProps {
  decisions: RevealedDecision[];
  totalDecisions: number;
  isTransitioning: boolean;
  nextTotalDecisions: number;
  showPlaceholders?: boolean;
}

export const DecisionTally: React.FC<DecisionTallyProps> = ({
  decisions,
  totalDecisions,
  isTransitioning,
  nextTotalDecisions,
  showPlaceholders = true,
}) => {
  const hasDecisions = totalDecisions > 0;
  const hasNextDecisions = nextTotalDecisions > 0;
  const hasVisibleContent = showPlaceholders
    ? hasDecisions
    : decisions.length > 0;

  const currentVisibleCount = Math.min(totalDecisions, TALLY_MAX_VISIBLE);
  const currentOverflow = Math.max(0, totalDecisions - TALLY_MAX_VISIBLE);
  const nextVisibleCount = Math.min(nextTotalDecisions, TALLY_MAX_VISIBLE);
  const nextOverflow = Math.max(0, nextTotalDecisions - TALLY_MAX_VISIBLE);

  const currentHeight = calculateTallyHeight(
    currentVisibleCount + (currentOverflow > 0 ? 1 : 0),
  );
  const nextHeight = calculateTallyHeight(
    nextVisibleCount + (nextOverflow > 0 ? 1 : 0),
  );
  const targetHeight = isTransitioning ? nextHeight : currentHeight;

  const heightSpring = useSpring({
    height: targetHeight,
    config: { tension: 200, friction: 20 },
  });

  const maxDecisionIcons =
    currentOverflow > 0 ? TALLY_MAX_VISIBLE - 1 : TALLY_MAX_VISIBLE;
  const visibleDecisions = decisions.slice(0, maxDecisionIcons);
  const emptyCount = showPlaceholders
    ? Math.max(
        0,
        Math.min(totalDecisions, maxDecisionIcons) - decisions.length,
      )
    : 0;

  if (!hasVisibleContent && !(isTransitioning && hasNextDecisions)) return null;

  return (
    <animated.div
      className={styles.legendTally}
      style={{ height: heightSpring.height, overflow: "hidden" }}
    >
      <div
        className={`${styles.tallyRow} ${isTransitioning ? styles.tallyFadeOut : ""}`}
      >
        {visibleDecisions.map((d) => (
          <span
            key={d.key}
            className={styles.tallyIcon}
            title={`${d.wordText}: ${d.decision}`}
          >
            <DecisionIcon decision={d} />
          </span>
        ))}
        {Array.from({ length: emptyCount }).map((_, idx) => (
          <span key={`empty-${idx}`} className={styles.tallyIcon}>
            <EmptyIcon />
          </span>
        ))}
        {currentOverflow > 0 && (
          <span className={styles.tallyOverflow}>+{currentOverflow}</span>
        )}
      </div>
      {isTransitioning && hasNextDecisions && showPlaceholders && (
        <div
          className={`${styles.tallyRow} ${styles.tallyRowOverlay} ${styles.tallyFadeIn}`}
        >
          {Array.from({
            length: Math.min(
              nextTotalDecisions,
              nextOverflow > 0 ? TALLY_MAX_VISIBLE - 1 : TALLY_MAX_VISIBLE,
            ),
          }).map((_, idx) => (
            <span key={`next-${idx}`} className={styles.tallyIcon}>
              <EmptyIcon />
            </span>
          ))}
          {nextOverflow > 0 && (
            <span className={styles.tallyOverflow}>+{nextOverflow}</span>
          )}
        </div>
      )}
    </animated.div>
  );
};
