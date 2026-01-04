import React from "react";
import { animated, useSpring, useTrail } from "@react-spring/web";
import { EvaluationResult, Decision, DECISION_COLORS } from "./mockData";
import styles from "./LLMEvaluatorVisualization.module.css";

interface SubagentCardsProps {
  currencyEvaluations: EvaluationResult[];
  metadataEvaluations: EvaluationResult[];
  isProcessing: boolean;
  showResults: boolean;
}

const DECISION_ICONS: Record<Decision, string> = {
  VALID: "✓",
  INVALID: "✗",
  NEEDS_REVIEW: "?",
};

const SubagentCards: React.FC<SubagentCardsProps> = ({
  currencyEvaluations,
  metadataEvaluations,
  isProcessing,
  showResults,
}) => {
  return (
    <div className={styles.cardsContainer}>
      <SubagentCard
        title="Currency Review"
        icon="💰"
        evaluations={currencyEvaluations}
        isProcessing={isProcessing}
        showResults={showResults}
        delay={0}
      />
      <SubagentCard
        title="Metadata Review"
        icon="📋"
        evaluations={metadataEvaluations}
        isProcessing={isProcessing}
        showResults={showResults}
        delay={200}
      />
    </div>
  );
};

interface SubagentCardProps {
  title: string;
  icon: string;
  evaluations: EvaluationResult[];
  isProcessing: boolean;
  showResults: boolean;
  delay: number;
}

const SubagentCard: React.FC<SubagentCardProps> = ({
  title,
  icon,
  evaluations,
  isProcessing,
  showResults,
  delay,
}) => {
  const cardSpring = useSpring({
    from: { opacity: 0, transform: "translateY(20px)" },
    to: { opacity: 1, transform: "translateY(0)" },
    delay,
    config: { tension: 200, friction: 20 },
  });

  // Count decisions for the badge
  const validCount = evaluations.filter((e) => e.decision === "VALID").length;
  const invalidCount = evaluations.filter((e) => e.decision === "INVALID").length;
  const reviewCount = evaluations.filter(
    (e) => e.decision === "NEEDS_REVIEW"
  ).length;

  // Determine overall badge
  const getBadge = (): { label: string; class: string } => {
    if (invalidCount > 0) {
      return { label: `${invalidCount} Invalid`, class: styles.badgeInvalid };
    }
    if (reviewCount > 0) {
      return { label: `${reviewCount} Review`, class: styles.badgeNeedsReview };
    }
    return { label: `${validCount} Valid`, class: styles.badgeValid };
  };

  const badge = getBadge();

  // Trail animation for evaluation items
  const trail = useTrail(evaluations.length, {
    from: { opacity: 0, transform: "translateX(-10px)" },
    to: {
      opacity: showResults ? 1 : 0,
      transform: showResults ? "translateX(0)" : "translateX(-10px)",
    },
    delay: delay + 300,
    config: { tension: 200, friction: 20 },
  });

  return (
    <animated.div
      className={`${styles.card} ${isProcessing ? styles.cardProcessing : ""}`}
      style={cardSpring}
    >
      <div className={styles.cardHeader}>
        <span className={styles.cardTitle}>
          <span className={styles.cardIcon}>{icon}</span>
          {title}
        </span>
        {showResults && (
          <span className={`${styles.cardBadge} ${badge.class}`}>
            {badge.label}
          </span>
        )}
      </div>

      <div className={styles.cardContent}>
        {isProcessing && !showResults ? (
          <LoadingSpinner />
        ) : (
          trail.map((style, index) => (
            <animated.div
              key={index}
              className={styles.evaluationItem}
              style={style}
            >
              <span className={styles.evaluationWord}>
                {evaluations[index].wordText}
              </span>
              <span className={styles.evaluationDecision}>
                <span
                  className={styles.decisionIcon}
                  style={{ color: DECISION_COLORS[evaluations[index].decision] }}
                >
                  {DECISION_ICONS[evaluations[index].decision]}
                </span>
              </span>
              <span className={styles.evaluationReasoning}>
                {evaluations[index].reasoning}
              </span>
            </animated.div>
          ))
        )}
      </div>
    </animated.div>
  );
};

const LoadingSpinner: React.FC = () => (
  <div className={styles.spinner}>
    <div className={styles.spinnerDot} />
    <div className={styles.spinnerDot} />
    <div className={styles.spinnerDot} />
  </div>
);

export default SubagentCards;
