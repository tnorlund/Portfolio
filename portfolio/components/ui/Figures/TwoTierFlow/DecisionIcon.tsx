import React from "react";
import type { RevealedDecision } from "./types";

const DECISION_COLORS: Record<string, string> = {
  VALID: "var(--color-green)",
  INVALID: "var(--color-red)",
  NEEDS_REVIEW: "var(--color-yellow)",
};

export const DecisionIcon: React.FC<{ decision: RevealedDecision }> = ({
  decision,
}) => {
  const bgColor = DECISION_COLORS[decision.decision];
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <circle cx="7" cy="7" r="6" fill={bgColor} />
      {decision.decision === "VALID" && (
        <path
          d="M4 7 L6 9.5 L10 5"
          fill="none"
          stroke="white"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
      {decision.decision === "INVALID" && (
        <g>
          <line
            x1="4.5"
            y1="4.5"
            x2="9.5"
            y2="9.5"
            stroke="white"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
          <line
            x1="9.5"
            y1="4.5"
            x2="4.5"
            y2="9.5"
            stroke="white"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
        </g>
      )}
      {decision.decision === "NEEDS_REVIEW" && (
        <g>
          <circle cx="7" cy="5" r="1.8" fill="white" />
          <path d="M3.5 11.5 Q3.5 8 7 8 Q10.5 8 10.5 11.5" fill="white" />
        </g>
      )}
    </svg>
  );
};

export const EmptyIcon: React.FC = () => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
    <circle
      cx="7"
      cy="7"
      r="5.5"
      fill="none"
      stroke="var(--text-color)"
      strokeWidth="1"
      opacity="0.3"
    />
  </svg>
);

export { DECISION_COLORS };
