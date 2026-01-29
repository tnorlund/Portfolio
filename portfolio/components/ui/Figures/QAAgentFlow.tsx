import React from "react";
import { useSpring, animated, config } from "@react-spring/web";

interface QAAgentFlowProps {
  /** Whether to auto-play the animation */
  autoPlay?: boolean;
}

// 5-node workflow step types
type StepType = "plan" | "agent" | "tools" | "shape" | "synthesize";

interface TraceStep {
  type: StepType;
  content: string;
  detail?: string;
  receipts?: ReceiptEvidence[];
  structuredData?: StructuredReceipt[];
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

interface StructuredReceipt {
  merchant: string;
  items: { name: string; amount: number }[];
}

// Real receipts with coffee products from the database
const COFFEE_RECEIPTS: ReceiptEvidence[] = [
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
    item: "Cappuccino + Americano",
    amount: 10.00,
    thumbnailKey: "assets/a762ca4d-860c-4116-abb1-e25f61145294_RECEIPT_00002.webp",
    width: 300,
    height: 550,
  },
  {
    imageId: "6f874726-d8fd-40a9-9023-91f56fda7dd2",
    merchant: "Blue Bottle",
    item: "Pour Over",
    amount: 5.25,
    thumbnailKey: "assets/6f874726-d8fd-40a9-9023-91f56fda7dd2_RECEIPT_00001.webp",
    width: 320,
    height: 600,
  },
  {
    imageId: "b50ba18b-7511-4a85-87cd-74aa5e147e10",
    merchant: "Le Pain Quotidien",
    item: "Iced Latte + Americano",
    amount: 11.25,
    thumbnailKey: "assets/b50ba18b-7511-4a85-87cd-74aa5e147e10_RECEIPT_00002.webp",
    width: 340,
    height: 750,
  },
];

// Structured receipt summaries (output of shape node)
const STRUCTURED_RECEIPTS: StructuredReceipt[] = [
  { merchant: "Sprouts", items: [{ name: "ORG BIRCHWOOD COFFEE", amount: 15.99 }] },
  { merchant: "La La Land", items: [{ name: "Large Americano", amount: 4.70 }, { name: "Cappuccino", amount: 5.30 }] },
  { merchant: "Blue Bottle", items: [{ name: "Pour Over", amount: 5.25 }] },
  { merchant: "Le Pain Quotidien", items: [{ name: "Iced Latte", amount: 6.25 }, { name: "Iced Americano", amount: 5.00 }] },
  { merchant: "Costco", items: [{ name: "KS ESPRESSO", amount: 15.89 }] },
];

// 5-node workflow trace: plan → agent ⟷ tools → shape → synthesize
const EXAMPLE_TRACE: TraceStep[] = [
  {
    type: "plan",
    content: "specific_item → semantic_hybrid",
    detail: "Classify question and choose retrieval strategy",
  },
  {
    type: "agent",
    content: "Search for coffee using text and semantic",
    detail: "Deciding which tools to call based on classification",
  },
  {
    type: "tools",
    content: 'search_receipts("COFFEE", "text") → 8 matches',
    detail: "Text search finds grocery store coffee",
  },
  {
    type: "agent",
    content: "Need semantic search for café drinks too",
    detail: "ReAct loop continues - more tools needed",
  },
  {
    type: "tools",
    content: 'semantic_search("coffee espresso latte") → 6 matches',
    detail: "Finds café purchases: La La Land, Blue Bottle...",
  },
  {
    type: "agent",
    content: "Have enough context, ready to shape",
    detail: "10 unique receipts retrieved",
  },
  {
    type: "shape",
    content: "10 receipts → 25 structured line items",
    detail: "Extract product names + amounts using word labels",
    structuredData: STRUCTURED_RECEIPTS,
  },
  {
    type: "synthesize",
    content: "You spent $58.38 on coffee",
    detail: "5 receipts with coffee items identified",
    receipts: COFFEE_RECEIPTS,
  },
];

const STEP_CONFIG: Record<StepType, { color: string; label: string; icon: string; node: string }> = {
  plan: { color: "#9C27B0", label: "Plan", icon: "📋", node: "1" },
  agent: { color: "#1E88E5", label: "Agent", icon: "🤖", node: "2" },
  tools: { color: "#43A047", label: "Tools", icon: "🔧", node: "3" },
  shape: { color: "#FB8C00", label: "Shape", icon: "📊", node: "4" },
  synthesize: { color: "#E53935", label: "Synthesize", icon: "✓", node: "5" },
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
          setTimeout(() => setActiveStep(-1), 3000);
          return prev;
        }
        return prev + 1;
      });
    }, 1500);

    return () => clearInterval(interval);
  }, [isPlaying, activeStep]);

  const questionSpring = useSpring({
    opacity: 1,
    config: config.gentle,
  });

  // Track which nodes are active for the flow diagram
  const getActiveNodes = () => {
    if (activeStep < 0) return new Set<string>();
    const step = EXAMPLE_TRACE[activeStep];
    const nodes = new Set<string>([step.type]);

    // Show connection from previous node
    if (activeStep > 0) {
      nodes.add(EXAMPLE_TRACE[activeStep - 1].type);
    }
    return nodes;
  };

  const activeNodes = getActiveNodes();

  return (
    <div
      style={{
        fontFamily: "var(--font-mono, monospace)",
        fontSize: "0.85rem",
        maxWidth: "600px",
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
          💬 "How much did I spend on coffee?"
        </div>
      </animated.div>

      {/* 5-Node Flow Diagram */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "0.5rem 0",
          marginBottom: "0.75rem",
          fontSize: "0.65rem",
        }}
      >
        {(["plan", "agent", "tools", "shape", "synthesize"] as StepType[]).map((node, idx) => {
          const cfg = STEP_CONFIG[node];
          const isActive = activeNodes.has(node);
          const isAgentToolsLoop = (node === "agent" || node === "tools") &&
            activeStep >= 1 && activeStep <= 5;

          return (
            <React.Fragment key={node}>
              {/* Node */}
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  opacity: isActive ? 1 : 0.3,
                  transition: "all 0.3s ease",
                }}
              >
                <div
                  style={{
                    width: "32px",
                    height: "32px",
                    borderRadius: "50%",
                    backgroundColor: isActive ? cfg.color : "var(--code-background)",
                    border: `2px solid ${cfg.color}`,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: "0.9rem",
                    boxShadow: isActive ? `0 0 8px ${cfg.color}50` : "none",
                  }}
                >
                  {cfg.icon}
                </div>
                <span
                  style={{
                    marginTop: "0.25rem",
                    color: cfg.color,
                    fontWeight: isActive ? 600 : 400,
                  }}
                >
                  {cfg.label}
                </span>
              </div>

              {/* Arrow between nodes */}
              {idx < 4 && (
                <div
                  style={{
                    flex: 1,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    position: "relative",
                  }}
                >
                  {/* Bidirectional arrow for agent ⟷ tools */}
                  {node === "agent" ? (
                    <div
                      style={{
                        fontSize: "0.8rem",
                        color: isAgentToolsLoop ? "#43A047" : "var(--text-color)",
                        opacity: isAgentToolsLoop ? 1 : 0.3,
                      }}
                    >
                      ⟷
                    </div>
                  ) : (
                    <div
                      style={{
                        fontSize: "0.7rem",
                        color: "var(--text-color)",
                        opacity: 0.3,
                      }}
                    >
                      →
                    </div>
                  )}
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>

      {/* Trace Steps */}
      <div style={{ position: "relative" }}>
        {EXAMPLE_TRACE.map((step, idx) => {
          const isActive = idx <= activeStep;
          const isCurrent = idx === activeStep;
          const cfg = STEP_CONFIG[step.type];

          // Visual grouping for agent/tools loop
          const isLoopStep = step.type === "agent" || step.type === "tools";
          const isInLoop = idx >= 1 && idx <= 5;

          return (
            <div
              key={idx}
              style={{
                opacity: isActive ? 1 : 0.25,
                transition: "all 0.3s ease",
                marginBottom: step.type === "shape" || step.type === "synthesize" ? "0.5rem" : "0.25rem",
                paddingLeft: isLoopStep && isInLoop ? "0.75rem" : "0",
                borderLeft: isLoopStep && isInLoop ? `2px solid ${isActive ? cfg.color : "var(--text-color)"}30` : "none",
                marginLeft: isLoopStep && isInLoop ? "0.5rem" : "0",
              }}
            >
              {/* Loop indicator for first agent step */}
              {idx === 1 && (
                <div
                  style={{
                    fontSize: "0.6rem",
                    color: "var(--text-color)",
                    opacity: 0.5,
                    marginBottom: "0.2rem",
                    marginLeft: "-0.25rem",
                  }}
                >
                  ↻ ReAct Loop
                </div>
              )}

              <div
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "0.5rem",
                  padding: "0.25rem 0",
                }}
              >
                {/* Node indicator */}
                <span
                  style={{
                    fontSize: "0.6rem",
                    fontWeight: 700,
                    color: cfg.color,
                    backgroundColor: `${cfg.color}20`,
                    padding: "0.1rem 0.3rem",
                    borderRadius: "3px",
                    minWidth: "1.2rem",
                    textAlign: "center",
                    filter: isCurrent ? `drop-shadow(0 0 4px ${cfg.color})` : "none",
                  }}
                >
                  {cfg.node}
                </span>

                {/* Content */}
                <div style={{ flex: 1 }}>
                  <span
                    style={{
                      color: "var(--text-color)",
                      fontSize: "0.8rem",
                      fontFamily: step.type === "tools" ? "var(--font-mono, monospace)" : "inherit",
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
                        marginTop: "0.1rem",
                      }}
                    >
                      {step.detail}
                    </div>
                  )}
                </div>
              </div>

              {/* Structured data preview for shape step */}
              {step.type === "shape" && step.structuredData && isActive && (
                <div
                  style={{
                    display: "flex",
                    gap: "0.4rem",
                    marginTop: "0.4rem",
                    flexWrap: "wrap",
                  }}
                >
                  {step.structuredData.slice(0, 3).map((receipt, rIdx) => (
                    <div
                      key={rIdx}
                      style={{
                        fontSize: "0.6rem",
                        padding: "0.3rem 0.4rem",
                        backgroundColor: "var(--code-background)",
                        borderRadius: "4px",
                        border: "1px solid rgba(var(--text-color-rgb, 0,0,0), 0.2)",
                      }}
                    >
                      <div style={{ fontWeight: 600, color: "#FB8C00" }}>{receipt.merchant}</div>
                      {receipt.items.map((item, iIdx) => (
                        <div key={iIdx} style={{ opacity: 0.7 }}>
                          {item.name}: ${item.amount.toFixed(2)}
                        </div>
                      ))}
                    </div>
                  ))}
                  <div
                    style={{
                      fontSize: "0.6rem",
                      padding: "0.3rem",
                      opacity: 0.5,
                      alignSelf: "center",
                    }}
                  >
                    +2 more
                  </div>
                </div>
              )}

              {/* Receipt thumbnails for synthesize step */}
              {step.type === "synthesize" && step.receipts && isActive && (
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
                    const thumbWidth = 50;
                    const aspectRatio = receipt.height / receipt.width;
                    const thumbHeight = Math.min(Math.round(thumbWidth * aspectRatio), 100);
                    const finalWidth = thumbHeight < Math.round(thumbWidth * aspectRatio)
                      ? Math.round(thumbHeight / aspectRatio)
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
                            height: `${thumbHeight}px`,
                            backgroundColor: "var(--code-background)",
                            borderRadius: "4px",
                            overflow: "hidden",
                            border: "1px solid var(--text-color)",
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
                              const target = e.target as HTMLImageElement;
                              target.style.display = "none";
                              target.parentElement!.innerHTML = '<span style="font-size: 1.2rem; opacity: 0.3; display: flex; align-items: center; justify-content: center; height: 100%;">🧾</span>';
                            }}
                          />
                        </div>
                        <div
                          style={{
                            fontSize: "0.55rem",
                            color: "var(--text-color)",
                            opacity: 0.7,
                            marginTop: "0.15rem",
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {receipt.merchant}
                        </div>
                        <div
                          style={{
                            fontSize: "0.6rem",
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
          }}
        >
          4 LLM calls · 2 tool invocations · 10 receipts shaped · $0.005 cost
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
