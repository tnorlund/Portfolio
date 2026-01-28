import React from "react";
import { useSpring, animated, config } from "@react-spring/web";

interface QAAgentFlowProps {
  /** Whether to auto-play the animation */
  autoPlay?: boolean;
}

// ReAct loop step types
type StepType = "plan" | "thought" | "action" | "observation" | "answer";

interface TraceStep {
  type: StepType;
  content: string;
  detail?: string;
  receipts?: ReceiptEvidence[];
}

interface ReceiptEvidence {
  imageId: string;
  merchant: string;
  item: string;
  amount: number;
  thumbnailKey: string;
  width: number;
  height: number;
}

// Real receipts with coffee products from the database (verified to have CDN images)
// CDN key format: assets/{image_id}_RECEIPT_{receipt_id:05d}.webp
// Width/height are estimates - actual values would come from DynamoDB Receipt entity
const COFFEE_RECEIPTS: ReceiptEvidence[] = [
  {
    imageId: "750e0675-f318-4d1d-994c-c330ff6cb3f3",
    merchant: "Costco",
    item: "KS ESPRESSO",
    amount: 15.89,
    thumbnailKey: "assets/750e0675-f318-4d1d-994c-c330ff6cb3f3_RECEIPT_00001.webp",
    width: 400,
    height: 850,
  },
  {
    imageId: "750e0675-f318-4d1d-994c-c330ff6cb3f3",
    merchant: "Sprouts",
    item: "ORG BIRCHWOOD COFFEE",
    amount: 15.99,
    thumbnailKey: "assets/750e0675-f318-4d1d-994c-c330ff6cb3f3_RECEIPT_00002.webp",
    width: 350,
    height: 900,
  },
  {
    imageId: "a762ca4d-860c-4116-abb1-e25f61145294",
    merchant: "La La Land",
    item: "Cappuccino",
    amount: 5.30,
    thumbnailKey: "assets/a762ca4d-860c-4116-abb1-e25f61145294_RECEIPT_00002.webp",
    width: 300,
    height: 550,
  },
  {
    imageId: "c985752b-1ef2-41cf-9707-76d84b7e3320",
    merchant: "Five07 Coffee",
    item: "Coffee Shop Visit",
    amount: 16.88,
    thumbnailKey: "assets/c985752b-1ef2-41cf-9707-76d84b7e3320_RECEIPT_00002.webp",
    width: 320,
    height: 700,
  },
];

// Real example trace from "How much did I spend on coffee?"
const EXAMPLE_TRACE: TraceStep[] = [
  {
    type: "plan",
    content: "Classify: category_query → hybrid_comprehensive",
    detail: "Use both text and semantic search to find all coffee purchases",
  },
  {
    type: "thought",
    content: "I need to search for coffee products using exact text matches first",
  },
  {
    type: "action",
    content: 'search_product_lines("COFFEE", "text")',
  },
  {
    type: "observation",
    content: "Found 8 items: BIRCHWOOD COFFEE $15.99, FRENCH ROAST $10.99, KIRKLAND COFFEE $24.99...",
  },
  {
    type: "thought",
    content: "Text search found grocery coffee. Now search semantically for café drinks",
  },
  {
    type: "action",
    content: 'search_product_lines("coffee drinks espresso latte", "semantic")',
  },
  {
    type: "observation",
    content: "Found 4 more: CAPPUCCINO $6.50, AMERICANO $5.25, LATTE $7.00, COLD BREW $5.50",
  },
  {
    type: "thought",
    content: "I have all coffee items. Total: $89.32 across 10 receipts. Submitting answer.",
  },
  {
    type: "answer",
    content: "You spent $89.32 on coffee this year",
    detail: "Including beans, espresso, lattes, cappuccinos, americanos, and cold brew",
    receipts: COFFEE_RECEIPTS,
  },
];

const STEP_CONFIG: Record<StepType, { color: string; label: string; icon: string }> = {
  plan: { color: "#9C27B0", label: "Plan", icon: "📋" },
  thought: { color: "#1E88E5", label: "Thought", icon: "💭" },
  action: { color: "#43A047", label: "Action", icon: "⚡" },
  observation: { color: "#FB8C00", label: "Observation", icon: "👁" },
  answer: { color: "#E53935", label: "Answer", icon: "✓" },
};

const CDN_BASE = "https://dev.tylernorlund.com";

const QAAgentFlow: React.FC<QAAgentFlowProps> = ({ autoPlay = true }) => {
  const [activeStep, setActiveStep] = React.useState(-1);
  const [isPlaying, setIsPlaying] = React.useState(autoPlay);

  // Auto-advance through steps
  React.useEffect(() => {
    if (!isPlaying) return;

    const interval = setInterval(() => {
      setActiveStep((prev) => {
        if (prev >= EXAMPLE_TRACE.length - 1) {
          // Pause at end, then reset
          setTimeout(() => setActiveStep(-1), 2000);
          return prev;
        }
        return prev + 1;
      });
    }, 1200);

    return () => clearInterval(interval);
  }, [isPlaying, activeStep]);

  const questionSpring = useSpring({
    opacity: 1,
    config: config.gentle,
  });

  return (
    <div
      style={{
        fontFamily: "var(--font-mono, monospace)",
        fontSize: "0.85rem",
        maxWidth: "540px",
        margin: "0 auto",
        padding: "0.5rem",
      }}
    >
      {/* Question */}
      <animated.div
        style={{
          ...questionSpring,
          padding: "0.6rem 0.8rem",
          backgroundColor: "var(--code-background)",
          borderRadius: "6px",
          marginBottom: "0.75rem",
          borderLeft: "3px solid #1E88E5",
        }}
      >
        <div style={{ color: "var(--text-color)", fontSize: "0.8rem" }}>
          💬 "How much did I spend on coffee this year?"
        </div>
      </animated.div>

      {/* Trace Steps */}
      <div style={{ position: "relative" }}>
        {EXAMPLE_TRACE.map((step, idx) => {
          const isActive = idx <= activeStep;
          const isCurrent = idx === activeStep;
          const cfg = STEP_CONFIG[step.type];

          // Group thought/action/observation visually
          const isReActStep = ["thought", "action", "observation"].includes(step.type);
          const prevIsReAct = idx > 0 && ["thought", "action", "observation"].includes(EXAMPLE_TRACE[idx - 1].type);
          const nextIsReAct = idx < EXAMPLE_TRACE.length - 1 && ["thought", "action", "observation"].includes(EXAMPLE_TRACE[idx + 1].type);

          const isLoopStart = step.type === "thought" && !prevIsReAct;
          const isLoopEnd = step.type === "observation" && !nextIsReAct;

          return (
            <div
              key={idx}
              style={{
                opacity: isActive ? 1 : 0.25,
                transition: "all 0.3s ease",
                marginBottom: isLoopEnd || step.type === "plan" || step.type === "answer" ? "0.5rem" : "0.25rem",
                paddingLeft: isReActStep ? "1rem" : "0",
                borderLeft: isReActStep ? `2px solid ${isActive ? cfg.color : "var(--text-color)"}20` : "none",
                marginLeft: isReActStep ? "0.5rem" : "0",
              }}
            >
              {/* Loop indicator */}
              {isLoopStart && (
                <div
                  style={{
                    fontSize: "0.65rem",
                    color: "var(--text-color)",
                    opacity: 0.5,
                    marginBottom: "0.25rem",
                    marginLeft: "-0.5rem",
                  }}
                >
                  ↻ ReAct Loop {idx < 5 ? "1" : "2"}
                </div>
              )}

              <div
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "0.5rem",
                  padding: "0.3rem 0",
                }}
              >
                {/* Icon */}
                <span
                  style={{
                    fontSize: "0.75rem",
                    opacity: isActive ? 1 : 0.5,
                    filter: isCurrent ? `drop-shadow(0 0 4px ${cfg.color})` : "none",
                  }}
                >
                  {cfg.icon}
                </span>

                {/* Content */}
                <div style={{ flex: 1 }}>
                  <span
                    style={{
                      fontSize: "0.65rem",
                      fontWeight: 600,
                      color: cfg.color,
                      textTransform: "uppercase",
                      marginRight: "0.4rem",
                    }}
                  >
                    {cfg.label}
                  </span>
                  <span
                    style={{
                      color: "var(--text-color)",
                      fontSize: "0.8rem",
                      fontFamily: step.type === "action" ? "var(--font-mono, monospace)" : "inherit",
                    }}
                  >
                    {step.content}
                  </span>
                  {step.detail && isActive && (
                    <div
                      style={{
                        fontSize: "0.7rem",
                        color: "var(--text-color)",
                        opacity: 0.6,
                        marginTop: "0.15rem",
                      }}
                    >
                      {step.detail}
                    </div>
                  )}
                </div>
              </div>

              {/* Receipt thumbnails for answer step */}
              {step.type === "answer" && step.receipts && isActive && (
                <div
                  style={{
                    display: "flex",
                    gap: "0.5rem",
                    marginTop: "0.5rem",
                    flexWrap: "wrap",
                    alignItems: "flex-end",
                  }}
                >
                  {step.receipts.slice(0, 4).map((receipt, rIdx) => {
                    // Calculate height based on aspect ratio, with fixed width
                    const thumbWidth = 55;
                    const aspectRatio = receipt.height / receipt.width;
                    const thumbHeight = Math.round(thumbWidth * aspectRatio);
                    // Cap height to reasonable max
                    const maxHeight = 120;
                    const finalHeight = Math.min(thumbHeight, maxHeight);
                    const finalWidth = finalHeight < thumbHeight
                      ? Math.round(finalHeight / aspectRatio)
                      : thumbWidth;

                    return (
                      <div
                        key={rIdx}
                        style={{
                          width: `${finalWidth}px`,
                          textAlign: "center",
                        }}
                      >
                        <div
                          style={{
                            width: `${finalWidth}px`,
                            height: `${finalHeight}px`,
                            backgroundColor: "var(--code-background)",
                            borderRadius: "4px",
                            overflow: "hidden",
                            border: "1px solid var(--text-color)",
                            borderOpacity: 0.2,
                          }}
                        >
                          <img
                            src={`${CDN_BASE}/${receipt.thumbnailKey}`}
                            alt={`${receipt.merchant} receipt`}
                            style={{
                              width: "100%",
                              height: "100%",
                              objectFit: "cover",
                            }}
                            onError={(e) => {
                              // Fallback to emoji if image fails to load
                              const target = e.target as HTMLImageElement;
                              target.style.display = "none";
                              target.parentElement!.innerHTML = '<span style="font-size: 1.5rem; opacity: 0.3; display: flex; align-items: center; justify-content: center; height: 100%;">🧾</span>';
                            }}
                          />
                        </div>
                        <div
                          style={{
                            fontSize: "0.6rem",
                            color: "var(--text-color)",
                            opacity: 0.7,
                            marginTop: "0.2rem",
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {receipt.merchant}
                        </div>
                        <div
                          style={{
                            fontSize: "0.65rem",
                            color: "#43A047",
                            fontWeight: 600,
                          }}
                        >
                          ${receipt.amount.toFixed(2)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Stats footer */}
      {activeStep >= EXAMPLE_TRACE.length - 1 && (
        <div
          style={{
            fontSize: "0.65rem",
            color: "var(--text-color)",
            opacity: 0.5,
            textAlign: "center",
            marginTop: "0.5rem",
            paddingTop: "0.5rem",
            borderTop: "1px solid var(--text-color)",
            borderOpacity: 0.1,
          }}
        >
          3 LLM calls · 2 tool invocations · $0.006 cost
        </div>
      )}

      {/* Controls */}
      <div style={{ textAlign: "center", marginTop: "0.75rem" }}>
        <button
          onClick={() => {
            if (isPlaying) {
              setIsPlaying(false);
            } else {
              setActiveStep(-1);
              setIsPlaying(true);
            }
          }}
          style={{
            padding: "0.3rem 0.6rem",
            fontSize: "0.7rem",
            backgroundColor: "transparent",
            border: "1px solid var(--text-color)",
            borderRadius: "4px",
            color: "var(--text-color)",
            cursor: "pointer",
            opacity: 0.5,
          }}
        >
          {isPlaying ? "⏸ Pause" : "▶ Replay"}
        </button>
      </div>
    </div>
  );
};

export default QAAgentFlow;
