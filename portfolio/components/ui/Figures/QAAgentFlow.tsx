import React from "react";
import { useSpring, animated, config } from "@react-spring/web";

/** Data for a single question from the QA cache API */
export interface QAQuestionData {
  question: string;
  questionIndex: number;
  traceId?: string;
  trace: TraceStep[];
  stats: {
    llmCalls: number;
    toolInvocations: number;
    receiptsProcessed: number;
    cost: number;
  };
}

interface QAAgentFlowProps {
  /** Whether to auto-play the animation */
  autoPlay?: boolean;
  /** Optional real data from the cache API; falls back to EXAMPLE_TRACE */
  questionData?: QAQuestionData;
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
  plan: { color: "var(--color-purple)", label: "Plan", icon: "📋", node: "1" },
  agent: { color: "var(--color-blue)", label: "Agent", icon: "🤖", node: "2" },
  tools: { color: "var(--color-green)", label: "Tools", icon: "🔧", node: "3" },
  shape: { color: "var(--color-orange)", label: "Shape", icon: "📊", node: "4" },
  synthesize: { color: "var(--color-red)", label: "Synthesize", icon: "✓", node: "5" },
};

const CDN_BASE = "https://dev.tylernorlund.com";

const QAAgentFlow: React.FC<QAAgentFlowProps> = ({ autoPlay = true, questionData }) => {
  const [activeStep, setActiveStep] = React.useState(-1);
  const [isPlaying, setIsPlaying] = React.useState(autoPlay);

  // Use real data when available, fall back to example trace
  const trace = questionData?.trace ?? EXAMPLE_TRACE;
  const questionText = questionData?.question ?? "How much did I spend on coffee?";
  const stats = questionData?.stats;

  // Auto-advance through steps
  React.useEffect(() => {
    if (!isPlaying) return;

    const interval = setInterval(() => {
      setActiveStep((prev) => {
        if (prev >= trace.length - 1) {
          // Pause at end, then reset
          setTimeout(() => setActiveStep(-1), 3000);
          return prev;
        }
        return prev + 1;
      });
    }, 1500);

    return () => clearInterval(interval);
  }, [isPlaying, activeStep, trace.length]);

  const questionSpring = useSpring({
    opacity: 1,
    config: config.gentle,
  });

  // Compute loop count: increments each time an agent step appears
  const loopCount = React.useMemo(() => {
    if (activeStep < 1) return 0;
    let count = 0;
    for (let i = 1; i <= Math.min(activeStep, trace.length - 1); i++) {
      if (trace[i].type === "agent") count++;
    }
    return count;
  }, [activeStep, trace]);

  // Determine which node is currently active
  const activeType: StepType | null = activeStep >= 0 && activeStep < trace.length ? trace[activeStep].type : null;
  // Determine the loop phase bounds dynamically based on trace content
  const loopEndIdx = trace.length > 0 ? trace.reduce((last, s, i) => (s.type === "agent" || s.type === "tools" ? i : last), -1) : 5;
  const inLoopPhase = activeStep >= 1 && activeStep <= loopEndIdx;

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
          borderLeft: "3px solid var(--color-blue)",
        }}
      >
        <div style={{ color: "var(--text-color)", fontSize: "0.8rem" }}>
          {`\uD83D\uDCAC "${questionText}"`}
        </div>
      </animated.div>

      {/* 5-Node SVG Flow Diagram — Tools below Agent with curved loop */}
      {(() => {
        // Main row: Plan, Agent, Shape, Synthesize (4 nodes horizontal)
        // Tools sits below Agent with curved arrows forming the loop
        const mainNodes: StepType[] = ["plan", "agent", "shape", "synthesize"];
        const svgW = 440;
        const svgH = 130;
        const rowY = 22;       // main row vertical center
        const nodeR = 16;
        const spacing = 100;
        const startX = 55;
        const mainXs = mainNodes.map((_, i) => startX + i * spacing);

        // Tools node position: below Agent
        const agentX = mainXs[1]; // 155
        const toolsX = agentX;
        const toolsY = 95;

        // Horizontal forward arrows on main row (index pairs into mainNodes)
        const forwardArrows: [number, number][] = [
          [0, 1], // plan → agent
          [1, 2], // agent → shape (activates when exiting the loop)
          [2, 3], // shape → synthesize
        ];

        const isForwardArrowActive = (fromIdx: number, toIdx: number): boolean => {
          if (activeStep < 0) return false;
          const toType = mainNodes[toIdx];
          if (activeType !== toType) return false;
          const fromType = mainNodes[fromIdx];
          // agent → shape: previous trace step is "agent" (step 5), fromType is "agent" ✓
          return activeStep > 0 && trace[activeStep - 1].type === fromType;
        };

        // Arrowhead size (defined early so edges can account for it)
        const ahLen = 7;
        const ahHalf = 4;
        const edgeGap = 4; // breathing room between node edge and line start/arrowhead tip

        // Loop arrows using cubic beziers with natural entry/exit angles
        // Departure angle from vertical (40°) sets where the path leaves each node
        const angle = 40 * Math.PI / 180;
        const edgeR = nodeR + edgeGap;                        // effective radius including gap
        const dx = Math.round(edgeR * Math.sin(angle));   // ~12 — horizontal offset
        const dy = Math.round(edgeR * Math.cos(angle));    // ~15 — vertical offset
        const armLen = 20; // control-point distance along tangent (circular arc approximation)
        const tx = Math.cos(angle); // tangent x component ~0.77
        const ty = Math.sin(angle); // tangent y component ~0.64

        // All edges follow the same rule:
        //   - arrowhead tip sits at the destination node's circle edge
        //   - line/path ends ahLen before the tip (so no line shows behind the arrowhead)
        //   - line/path starts at the source node's circle edge

        // Right arc: Agent bottom-right → Tools top-right
        const rsx = agentX + dx, rsy = rowY + dy;                          // start (on Agent circle)
        const rTipX = agentX + dx, rTipY = toolsY - dy;                    // arrowhead tip (on Tools circle)
        const rex = rTipX + ahLen * tx, rey = rTipY - ahLen * ty;          // path end (ahLen back along tangent)
        const downD = [
          `M ${rsx} ${rsy}`,
          `C ${rsx + armLen * tx} ${rsy + armLen * ty},`,
          `${rex + armLen * tx} ${rey - armLen * ty},`,
          `${rex} ${rey}`,
        ].join(" ");

        // Left arc: Tools top-left → Agent bottom-left (mirror)
        const lsx = agentX - dx, lsy = toolsY - dy;                        // start (on Tools circle)
        const lTipX = agentX - dx, lTipY = rowY + dy;                      // arrowhead tip (on Agent circle)
        const lex = lTipX - ahLen * tx, ley = lTipY + ahLen * ty;          // path end (ahLen back along tangent)
        const upD = [
          `M ${lsx} ${lsy}`,
          `C ${lsx - armLen * tx} ${lsy - armLen * ty},`,
          `${lex - armLen * tx} ${ley + armLen * ty},`,
          `${lex} ${ley}`,
        ].join(" ");

        // Activation states
        const downActive = activeType === "tools" && inLoopPhase;
        const upActive = activeType === "agent" && activeStep > 1 && inLoopPhase;
        const loopVisible = inLoopPhase || activeStep === loopEndIdx + 1;

        // Badge position: centered between the arc departure points (Agent bottom and Tools top)
        const badgeX = agentX;
        const badgeY = (rsy + rTipY) / 2;

        // Arrowhead angles (degrees) for loop curves, derived from the bezier tangent at t=1
        const downArrowAngle = 180 - 40; // 140° — points down-left into Tools
        const upArrowAngle = -40;        // -40° — points up-right into Agent

        // Helper: render an arrowhead triangle at (tipX, tipY) rotated by angleDeg
        const renderArrowhead = (tipX: number, tipY: number, angleDeg: number, color: string) => (
          <polygon
            points={`0,0 ${-ahLen},${-ahHalf} ${-ahLen},${ahHalf}`}
            fill={color}
            transform={`translate(${tipX},${tipY}) rotate(${angleDeg})`}
          />
        );

        // Clock-fill animation circumference
        const circumference = Math.PI * nodeR;

        // Helper: render a node circle at arbitrary (cx, cy)
        const renderNode = (node: StepType, cx: number, cy: number) => {
          const cfg = STEP_CONFIG[node];
          const isActive = activeType === node;
          const wasVisited = activeStep >= 0 && trace.slice(0, activeStep + 1).some((s) => s.type === node);
          return (
            <g key={node} style={{ transition: "opacity 0.3s ease" }} opacity={isActive ? 1 : wasVisited ? 0.85 : 0.3}>
              {/* Base circle: filled with color when visited (but not active) */}
              <circle
                cx={cx} cy={cy} r={nodeR}
                fill={wasVisited && !isActive ? cfg.color : "var(--code-background)"}
                stroke={cfg.color} strokeWidth={2}
              />
              {/* Clock-fill overlay: sweeps clockwise when active */}
              {isActive && (
                <circle
                  key={activeStep}
                  cx={cx} cy={cy}
                  r={nodeR / 2}
                  fill="none"
                  stroke={cfg.color}
                  strokeWidth={nodeR}
                  strokeDasharray={circumference}
                  strokeDashoffset={circumference}
                  transform={`rotate(-90 ${cx} ${cy})`}
                  style={{ animation: "clockFill 1.3s linear forwards" }}
                />
              )}
            </g>
          );
        };

        return (
          <div style={{ marginBottom: "0.75rem", textAlign: "center" }}>
            <svg
              width="100%"
              height={svgH}
              viewBox={`0 0 ${svgW} ${svgH}`}
              style={{ maxWidth: `${svgW}px`, overflow: "visible" }}
            >
              <defs>
                <style>{`
                  @keyframes clockFill {
                    from { stroke-dashoffset: ${circumference}; }
                    to   { stroke-dashoffset: 0; }
                  }
                `}</style>
              </defs>
              {/* Horizontal forward arrows on main row */}
              {forwardArrows.map(([fromIdx, toIdx]) => {
                const x1 = mainXs[fromIdx] + nodeR + edgeGap;     // start at source edge + gap
                const tipX = mainXs[toIdx] - nodeR - edgeGap;   // arrowhead tip at dest edge - gap
                const x2 = tipX - ahLen;                          // line ends ahLen before tip
                const active = isForwardArrowActive(fromIdx, toIdx);
                const color = active ? STEP_CONFIG[mainNodes[toIdx]].color : "var(--text-color)";
                return (
                  <g key={`fwd-${fromIdx}-${toIdx}`} opacity={active ? 1 : 0.2} style={{ transition: "opacity 0.3s ease" }}>
                    <line
                      x1={x1} y1={rowY} x2={x2} y2={rowY}
                      stroke={color} strokeWidth={active ? 2.5 : 2}
                    />
                    {renderArrowhead(tipX, rowY, 0, color)}
                  </g>
                );
              })}

              {/* Loop: Agent → Tools (right arc) */}
              <g opacity={downActive ? 1 : loopVisible ? 0.35 : 0.15} style={{ transition: "opacity 0.3s ease" }}>
                <path
                  d={downD} fill="none"
                  stroke={downActive ? "var(--color-green)" : "var(--text-color)"}
                  strokeWidth={downActive ? 2.5 : 2}
                />
                {renderArrowhead(rTipX, rTipY, downArrowAngle, downActive ? "var(--color-green)" : "var(--text-color)")}
              </g>

              {/* Loop: Tools → Agent (left arc) */}
              <g opacity={upActive ? 1 : loopVisible ? 0.35 : 0.15} style={{ transition: "opacity 0.3s ease" }}>
                <path
                  d={upD} fill="none"
                  stroke={upActive ? "var(--color-blue)" : "var(--text-color)"}
                  strokeWidth={upActive ? 2.5 : 2}
                />
                {renderArrowhead(lTipX, lTipY, upArrowAngle, upActive ? "var(--color-blue)" : "var(--text-color)")}
              </g>

              {/* Iteration badge ×N */}
              {loopCount > 0 && (
                <g style={{ transition: "opacity 0.3s ease" }} opacity={loopVisible ? 1 : 0}>
                  <rect
                    x={badgeX - 14} y={badgeY - 9}
                    width={28} height={18} rx={4}
                    fill="var(--code-background)" stroke="var(--color-green)" strokeWidth={1} opacity={0.9}
                  />
                  <text
                    x={badgeX} y={badgeY}
                    textAnchor="middle" dominantBaseline="central" fontSize="11" fontWeight={700}
                    fontFamily="var(--font-mono, monospace)" fill="var(--color-green)"
                  >
                    {`×${loopCount}`}
                  </text>
                </g>
              )}

              {/* Main row nodes */}
              {mainNodes.map((node, idx) => renderNode(node, mainXs[idx], rowY))}

              {/* Tools node (below Agent) */}
              {renderNode("tools", toolsX, toolsY)}
            </svg>
          </div>
        );
      })()}

      {/* Trace Steps */}
      <div style={{ position: "relative" }}>
        {trace.map((step, idx) => {
          const isActive = idx <= activeStep;
          const isCurrent = idx === activeStep;
          const cfg = STEP_CONFIG[step.type];

          // Visual grouping for agent/tools loop
          const isLoopStep = step.type === "agent" || step.type === "tools";
          const isInLoop = idx >= 1 && idx <= loopEndIdx;

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
                      <div style={{ fontWeight: 600, color: "var(--color-orange)" }}>{receipt.merchant}</div>
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
                            color: "var(--color-green)",
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
      {activeStep >= trace.length - 1 && (
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
          {stats
            ? `${stats.llmCalls} LLM calls · ${stats.toolInvocations} tool invocations · ${stats.receiptsProcessed} receipts shaped · $${stats.cost.toFixed(3)} cost`
            : "4 LLM calls · 2 tool invocations · 10 receipts shaped · $0.005 cost"}
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
