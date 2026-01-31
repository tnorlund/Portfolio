import React, { useRef, useEffect, useState } from "react";

// Sample questions that could be asked about receipts
const QUESTIONS = [
  "How much did I spend on groceries last month?",
  "What was my total spending at Costco?",
  "Show me all receipts with dairy products",
  "How much did I spend on coffee this year?",
  "What's my average grocery bill?",
  "Find all receipts from restaurants",
  "How much tax did I pay last quarter?",
  "What was my largest purchase this month?",
  "Show receipts with items over $50",
  "How much did I spend on organic products?",
  "What's the total for all gas station visits?",
  "Find all receipts with produce items",
  "How much did I spend on snacks?",
  "Show me pharmacy receipts from last week",
  "What was my food spending trend over 6 months?",
  "Find duplicate purchases across stores",
  "How much did I spend on beverages?",
  "Show all receipts with discounts applied",
  "What's the breakdown by store category?",
  "Find receipts where I bought milk",
  "How much did I spend on household items?",
  "Show receipts from the past 7 days",
  "What's my monthly spending average?",
  "Find all breakfast item purchases",
  "How much did I spend on pet food?",
  "Show receipts with loyalty points earned",
  "What was my cheapest grocery trip?",
  "Find all receipts with returns or refunds",
  "How much did I spend on frozen foods?",
  "Show me spending patterns by day of week",
  "What's my average tip at restaurants?",
  "Find receipts with handwritten notes",
];

interface QuestionMarqueeProps {
  /** Speed of scroll animation in seconds per full cycle */
  speed?: number;
  /** Number of rows to display */
  rows?: number;
  /** Called when a question pill is clicked, with the question's global index */
  onQuestionClick?: (index: number) => void;
  /** Currently active question index — bolds the pill and freezes its row */
  activeQuestion?: number;
}

/**
 * A marquee component that displays receipt-related questions
 * scrolling horizontally in alternating directions across multiple rows.
 */
const QuestionMarquee: React.FC<QuestionMarqueeProps> = ({
  speed = 30,
  rows = 4,
  onQuestionClick,
  activeQuestion,
}) => {
  // Split questions across rows
  const questionsPerRow = Math.ceil(QUESTIONS.length / rows);
  const rowQuestions = Array.from({ length: rows }, (_, i) =>
    QUESTIONS.slice(i * questionsPerRow, (i + 1) * questionsPerRow)
  );

  // Determine which row contains the active question
  const activeRow = activeQuestion != null ? Math.floor(activeQuestion / questionsPerRow) : -1;

  const containerRef = useRef<HTMLDivElement>(null);
  const [centerOffset, setCenterOffset] = useState<number | null>(null);

  // Compute translateX to center the active pill in the visible container
  useEffect(() => {
    if (activeRow < 0) {
      setCenterOffset(null);
      return;
    }

    const raf = requestAnimationFrame(() => {
      const container = containerRef.current;
      if (!container) return;

      const pill = container.querySelector('[data-active="true"]') as HTMLElement | null;
      if (!pill) return;

      const containerW = container.offsetWidth;
      const pillLeft = pill.offsetLeft;
      const pillW = pill.offsetWidth;
      setCenterOffset(containerW / 2 - pillLeft - pillW / 2);
    });

    return () => cancelAnimationFrame(raf);
  }, [activeQuestion, activeRow]);

  return (
    <div
      ref={containerRef}
      style={{
        width: "100%",
        overflow: "hidden",
        padding: "1rem 0",
        // Fade in/out on left and right edges
        maskImage:
          "linear-gradient(to right, transparent 0%, black 10%, black 90%, transparent 100%)",
        WebkitMaskImage:
          "linear-gradient(to right, transparent 0%, black 10%, black 90%, transparent 100%)",
      }}
    >
      <style>
        {`
          @keyframes scrollLeft {
            0% {
              transform: translateX(0);
            }
            100% {
              transform: translateX(-50%);
            }
          }

          @keyframes scrollRight {
            0% {
              transform: translateX(-50%);
            }
            100% {
              transform: translateX(0);
            }
          }

          .marquee-row {
            display: flex;
            white-space: nowrap;
            margin: 0.5rem 0;
            width: fit-content;
          }

          .marquee-row-left {
            animation: scrollLeft var(--scroll-speed) linear infinite;
          }

          .marquee-row-right {
            animation: scrollRight var(--scroll-speed) linear infinite;
          }

          .marquee-row:hover {
            animation-play-state: paused;
          }

          .question-pill {
            display: inline-block;
            padding: 0.4rem 0.8rem;
            margin: 0 0.4rem;
            border: 1px solid var(--color-text);
            border-radius: 4px;
            font-size: 0.85rem;
            color: var(--color-text);
            background: var(--color-background);
          }
        `}
      </style>

      {rowQuestions.map((questions, rowIndex) => {
        const isReversed = rowIndex % 2 === 1;
        const isRowFrozen = rowIndex === activeRow;
        // Duplicate questions to create seamless loop
        const duplicatedQuestions = [...questions, ...questions];

        return (
          <div
            key={rowIndex}
            className={`marquee-row ${isRowFrozen ? "" : (isReversed ? "marquee-row-right" : "marquee-row-left")}`}
            style={{
              "--scroll-speed": `${speed + rowIndex * 3}s`,
              ...(isRowFrozen
                ? {
                    transform: `translateX(${centerOffset ?? 0}px)`,
                    transition: "transform 0.6s ease",
                  }
                : {}),
            } as React.CSSProperties}
          >
            {duplicatedQuestions.map((question, qIndex) => {
              // Map duplicated index back to original QUESTIONS index
              const originalIdx = (rowIndex * questionsPerRow) + (qIndex % questions.length);
              const isActive = originalIdx === activeQuestion;
              return (
                <span
                  key={`${rowIndex}-${qIndex}`}
                  className="question-pill"
                  data-active={isActive ? "true" : undefined}
                  onClick={onQuestionClick ? () => onQuestionClick(originalIdx) : undefined}
                  style={{
                    cursor: onQuestionClick ? "pointer" : undefined,
                    fontWeight: isActive ? 700 : undefined,
                    fontSize: isActive ? "1.1rem" : undefined,
                    borderColor: isActive ? "var(--color-blue)" : undefined,
                    borderWidth: isActive ? "2px" : undefined,
                  }}
                >
                  {question}
                </span>
              );
            })}
          </div>
        );
      })}
    </div>
  );
};

export default QuestionMarquee;
