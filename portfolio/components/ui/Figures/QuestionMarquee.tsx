import React from "react";

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
}

/**
 * A marquee component that displays receipt-related questions
 * scrolling horizontally in alternating directions across multiple rows.
 */
const QuestionMarquee: React.FC<QuestionMarqueeProps> = ({
  speed = 30,
  rows = 4,
}) => {
  // Split questions across rows
  const questionsPerRow = Math.ceil(QUESTIONS.length / rows);
  const rowQuestions = Array.from({ length: rows }, (_, i) =>
    QUESTIONS.slice(i * questionsPerRow, (i + 1) * questionsPerRow)
  );

  return (
    <div
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
        // Duplicate questions to create seamless loop
        const duplicatedQuestions = [...questions, ...questions];

        return (
          <div
            key={rowIndex}
            className={`marquee-row ${isReversed ? "marquee-row-right" : "marquee-row-left"}`}
            style={{
              // @ts-expect-error - CSS custom property
              "--scroll-speed": `${speed + rowIndex * 3}s`,
            }}
          >
            {duplicatedQuestions.map((question, qIndex) => (
              <span key={`${rowIndex}-${qIndex}`} className="question-pill">
                {question}
              </span>
            ))}
          </div>
        );
      })}
    </div>
  );
};

export default QuestionMarquee;
