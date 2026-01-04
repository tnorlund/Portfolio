import React, { useState } from "react";
import { animated, useSpring } from "@react-spring/web";
import { ReceiptWord, LABEL_COLORS } from "./mockData";
import styles from "./GeometricAnomalyVisualization.module.css";

interface ReceiptViewProps {
  words: ReceiptWord[];
  animationProgress: number; // 0-1, controls which words are visible
  highlightedWordId: string | null;
  onWordHover: (wordId: string | null) => void;
  onWordClick: (wordId: string) => void;
}

const ReceiptView: React.FC<ReceiptViewProps> = ({
  words,
  animationProgress,
  highlightedWordId,
  onWordHover,
  onWordClick,
}) => {
  const [tooltipWord, setTooltipWord] = useState<ReceiptWord | null>(null);

  // Calculate how many words to show based on animation progress
  const visibleCount = Math.floor(words.length * animationProgress);

  const handleMouseEnter = (word: ReceiptWord) => {
    setTooltipWord(word);
    onWordHover(word.id);
  };

  const handleMouseLeave = () => {
    setTooltipWord(null);
    onWordHover(null);
  };

  return (
    <div className={styles.receiptPanel}>
      <h4 className={styles.panelTitle}>Receipt View</h4>
      <div className={styles.receiptContainer}>
        {words.slice(0, visibleCount).map((word, index) => (
          <WordBox
            key={word.id}
            word={word}
            index={index}
            isHighlighted={highlightedWordId === word.id}
            onMouseEnter={() => handleMouseEnter(word)}
            onMouseLeave={handleMouseLeave}
            onClick={() => onWordClick(word.id)}
          />
        ))}

        {/* Tooltip */}
        {tooltipWord && tooltipWord.isFlagged && (
          <div
            className={styles.wordTooltip}
            style={{
              left: `${tooltipWord.x * 100 + tooltipWord.width * 50}%`,
              top: `${tooltipWord.y * 100}%`,
              transform: "translateX(-50%) translateY(-100%)",
            }}
          >
            <div className={styles.tooltipLabel}>
              {tooltipWord.anomalyType?.replace(/_/g, " ").toUpperCase()}
            </div>
            <div className={styles.tooltipReasoning}>
              {tooltipWord.reasoning}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

interface WordBoxProps {
  word: ReceiptWord;
  index: number;
  isHighlighted: boolean;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  onClick: () => void;
}

const WordBox: React.FC<WordBoxProps> = ({
  word,
  index,
  isHighlighted,
  onMouseEnter,
  onMouseLeave,
  onClick,
}) => {
  const labelColor = word.label
    ? LABEL_COLORS[word.label] || "var(--text-color)"
    : "var(--text-color)";

  // Staggered fade-in animation
  const spring = useSpring({
    from: { opacity: 0, scale: 0.8 },
    to: { opacity: 1, scale: 1 },
    delay: index * 30,
    config: { tension: 200, friction: 20 },
  });

  // Highlight animation for flagged/selected words
  const highlightSpring = useSpring({
    scale: isHighlighted ? 1.15 : 1,
    config: { tension: 300, friction: 20 },
  });

  return (
    <animated.div
      className={`${styles.receiptWord} ${word.isFlagged ? styles.receiptWordFlagged : ""}`}
      style={{
        left: `${word.x * 100}%`,
        top: `${word.y * 100}%`,
        backgroundColor: word.label
          ? `color-mix(in srgb, ${labelColor} 20%, transparent)`
          : "transparent",
        borderColor: word.label ? labelColor : "transparent",
        color: word.label ? labelColor : "var(--text-color)",
        opacity: spring.opacity,
        transform: highlightSpring.scale.to(
          (s) => `scale(${s * spring.scale.get()})`
        ),
      }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      onClick={onClick}
    >
      {word.text}
    </animated.div>
  );
};

export default ReceiptView;
