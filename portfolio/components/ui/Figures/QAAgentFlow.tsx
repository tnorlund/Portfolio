import React from "react";
import ReactMarkdown from "react-markdown";
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
  /** Called when one animation cycle completes (after the end-of-trace pause) */
  onCycleComplete?: () => void;
  /** Content rendered at the top of the card (e.g. QuestionMarquee) */
  children?: React.ReactNode;
}

// 5-node workflow step types
type StepType = "plan" | "agent" | "tools" | "shape" | "synthesize";

interface TraceStep {
  type: StepType;
  content: string;
  detail?: string;
  /** Step duration in milliseconds (from LangSmith trace timestamps) */
  durationMs?: number;
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
    durationMs: 1200,
  },
  {
    type: "agent",
    content: "Search for coffee using text and semantic",
    detail: "Deciding which tools to call based on classification",
    durationMs: 3400,
  },
  {
    type: "tools",
    content: 'search_receipts("COFFEE", "text") → 8 matches',
    detail: "Text search finds grocery store coffee",
    durationMs: 800,
  },
  {
    type: "agent",
    content: "Need semantic search for café drinks too",
    detail: "ReAct loop continues - more tools needed",
    durationMs: 2100,
  },
  {
    type: "tools",
    content: 'semantic_search("coffee espresso latte") → 6 matches',
    detail: "Finds café purchases: La La Land, Blue Bottle...",
    durationMs: 600,
  },
  {
    type: "agent",
    content: "Have enough context, ready to shape",
    detail: "10 unique receipts retrieved",
    durationMs: 1800,
  },
  {
    type: "shape",
    content: "10 receipts → 25 structured line items",
    detail: "Extract product names + amounts using word labels",
    durationMs: 4500,
    structuredData: STRUCTURED_RECEIPTS,
  },
  {
    type: "synthesize",
    content: "You spent $58.38 on coffee",
    detail: "5 receipts with coffee items identified",
    durationMs: 5200,
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

/** Default step duration (ms) when durationMs is not available */
const DEFAULT_STEP_MS = 1500;
/** Target total animation time (ms) for proportional scaling */
const TARGET_TOTAL_MS = 12000;
/** Minimum per-step duration to keep the animation readable */
const MIN_STEP_MS = 600;
/** Maximum per-step duration to prevent a single slow step dominating */
const MAX_STEP_MS = 3500;

const QAAgentFlow: React.FC<QAAgentFlowProps> = ({ autoPlay = true, questionData, onCycleComplete, children }) => {
  const [activeStep, setActiveStep] = React.useState(-1);
  const [isPlaying, setIsPlaying] = React.useState(autoPlay);
  const [showAnswer, setShowAnswer] = React.useState(false);

  // Use real data when available, fall back to example trace
  const trace = questionData?.trace ?? EXAMPLE_TRACE;
  const stats = questionData?.stats;

  // Reset animation when question changes (use index to avoid stale-data issues on mobile)
  const questionIndex = questionData?.questionIndex ?? -1;
  React.useEffect(() => {
    setActiveStep(-1);
    setShowAnswer(false);
    setIsPlaying(true);
  }, [questionIndex]);

  // Compute per-step animation durations from durationMs, scaled proportionally
  const stepDurations = React.useMemo(() => {
    const hasTiming = trace.some((s) => s.durationMs != null && s.durationMs > 0);
    if (!hasTiming) return trace.map(() => DEFAULT_STEP_MS);

    const rawMs = trace.map((s) => s.durationMs ?? DEFAULT_STEP_MS);
    const rawTotal = rawMs.reduce((sum, d) => sum + d, 0);
    if (rawTotal === 0) return trace.map(() => DEFAULT_STEP_MS);

    const scale = TARGET_TOTAL_MS / rawTotal;
    return rawMs.map((d) => Math.max(MIN_STEP_MS, Math.min(MAX_STEP_MS, Math.round(d * scale))));
  }, [trace]);

  // Current step's animation duration (for clock-fill sync)
  const currentStepDuration =
    activeStep >= 0 && activeStep < stepDurations.length
      ? stepDurations[activeStep]
      : DEFAULT_STEP_MS;

  // Auto-advance through steps using per-step durations
  React.useEffect(() => {
    if (!isPlaying) return;

    if (activeStep >= trace.length - 1) {
      // Last step reached — reveal answer after its duration, then hold 5s
      if (!showAnswer) {
        const id = setTimeout(() => setShowAnswer(true), stepDurations[trace.length - 1]);
        return () => clearTimeout(id);
      }
      const id = setTimeout(() => {
        // Reset animation before advancing so the next cycle always starts
        // fresh — even when consecutive null-data states produce the same
        // questionIndex (-1 → -1) and the questionIndex effect doesn't fire.
        setActiveStep(-1);
        setShowAnswer(false);
        onCycleComplete?.();
      }, 10000);
      return () => clearTimeout(id);
    }

    const delay = activeStep < 0 ? 400 : stepDurations[activeStep];
    const id = setTimeout(() => setActiveStep((prev) => prev + 1), delay);
    return () => clearTimeout(id);
  }, [isPlaying, activeStep, trace.length, stepDurations, showAnswer, onCycleComplete]);

  const questionSpring = useSpring({
    opacity: 1,
    config: config.gentle,
  });

  // Measure answer content height and spring the container open
  const answerRef = React.useRef<HTMLDivElement>(null);
  const [measuredHeight, setMeasuredHeight] = React.useState(0);

  React.useEffect(() => {
    if (showAnswer && answerRef.current) {
      const raf = requestAnimationFrame(() => {
        if (answerRef.current) {
          setMeasuredHeight(answerRef.current.scrollHeight);
        }
      });
      return () => cancelAnimationFrame(raf);
    }
    setMeasuredHeight(0);
  }, [showAnswer, questionIndex]);

  const answerHeightSpring = useSpring({
    height: measuredHeight > 0 ? measuredHeight : 60,
    config: config.gentle,
  });

  // Compute flame-graph bar target (cumulative width% through the current step)
  const barWidths = React.useMemo(() => {
    const totalMs = trace.reduce((sum, s) => sum + (s.durationMs ?? 0), 0);
    return trace.map((s) =>
      totalMs > 0 ? ((s.durationMs ?? 0) / totalMs) * 100 : 100 / trace.length,
    );
  }, [trace]);

  const barTarget = React.useMemo(() => {
    if (activeStep < 0) return 0;
    if (activeStep >= trace.length - 1) return 100;
    return barWidths.slice(0, activeStep + 1).reduce((a, b) => a + b, 0);
  }, [activeStep, trace.length, barWidths]);

  const barSpring = useSpring({
    progress: barTarget,
    config: { duration: activeStep < 0 ? 0 : currentStepDuration },
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
        fontSize: "0.85rem",
        maxWidth: "1000px",
        margin: "0 auto",
        padding: "0.5rem",
      }}
    >
      {/* Flame Graph Timeline + Node Diagram */}
      {(() => {
        const totalMs = trace.reduce((sum, s) => sum + (s.durationMs ?? 0), 0);

        const formatMs = (ms: number): string =>
          ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms.toFixed(1)}ms`;

        // Deduplicate step types for the legend (e.g. multiple agent/tools steps)
        const uniqueTypes: StepType[] = [];
        for (const step of trace) {
          if (!uniqueTypes.includes(step.type)) uniqueTypes.push(step.type);
        }

        // Compute cumulative boundaries for pie-fill per legend entry
        const legendEntries = uniqueTypes.map((type) => {
          const cfg = STEP_CONFIG[type];
          const stepIndices = trace
            .map((s, i) => (s.type === type ? i : -1))
            .filter((i) => i >= 0);
          const visitedCount = stepIndices.filter((i) => i <= activeStep).length;
          const fillPercent =
            stepIndices.length > 0
              ? (visitedCount / stepIndices.length) * 100
              : 0;
          return { type, cfg, fillPercent };
        });

        // --- SVG node diagram constants ---
        const mainNodes: StepType[] = ["plan", "agent", "shape", "synthesize"];
        const S = 1.5;
        const nodeR = Math.round(16 * S);
        const spacing = Math.round(70 * S);
        const startX = nodeR + Math.round(10 * S);
        const mainXs = mainNodes.map((_, i) => startX + i * spacing);
        const svgW = mainXs[mainXs.length - 1] + nodeR + Math.round(10 * S);
        const toolsY = Math.round(95 * S);
        const svgH = toolsY + nodeR + Math.round(10 * S);
        const rowY = Math.round(22 * S);

        const agentX = mainXs[1];
        const toolsX = agentX;

        const forwardArrows: [number, number][] = [
          [0, 1], [1, 2], [2, 3],
        ];

        const isForwardArrowActive = (fromIdx: number, toIdx: number): boolean => {
          if (activeStep < 0) return false;
          const toType = mainNodes[toIdx];
          if (activeType !== toType) return false;
          const fromType = mainNodes[fromIdx];
          return activeStep > 0 && trace[activeStep - 1].type === fromType;
        };

        const ahLen = Math.round(7 * S);
        const ahHalf = Math.round(4 * S);
        const edgeGap = Math.round(4 * S);

        const angle = 40 * Math.PI / 180;
        const edgeR = nodeR + edgeGap;
        const dx = Math.round(edgeR * Math.sin(angle));
        const dy = Math.round(edgeR * Math.cos(angle));
        const armLen = Math.round(20 * S);
        const tx = Math.cos(angle);
        const ty = Math.sin(angle);

        const rsx = agentX + dx, rsy = rowY + dy;
        const rTipX = agentX + dx, rTipY = toolsY - dy;
        const rex = rTipX + ahLen * tx, rey = rTipY - ahLen * ty;
        const downD = [
          `M ${rsx} ${rsy}`,
          `C ${rsx + armLen * tx} ${rsy + armLen * ty},`,
          `${rex + armLen * tx} ${rey - armLen * ty},`,
          `${rex} ${rey}`,
        ].join(" ");

        const lsx = agentX - dx, lsy = toolsY - dy;
        const lTipX = agentX - dx, lTipY = rowY + dy;
        const lex = lTipX - ahLen * tx, ley = lTipY + ahLen * ty;
        const upD = [
          `M ${lsx} ${lsy}`,
          `C ${lsx - armLen * tx} ${lsy - armLen * ty},`,
          `${lex - armLen * tx} ${ley + armLen * ty},`,
          `${lex} ${ley}`,
        ].join(" ");

        const downActive = activeType === "tools" && inLoopPhase;
        const upActive = activeType === "agent" && activeStep > 1 && inLoopPhase;
        const loopVisible = inLoopPhase || activeStep === loopEndIdx + 1;

        const badgeX = agentX;
        const badgeY = (rsy + rTipY) / 2;

        const downArrowAngle = 180 - 40;
        const upArrowAngle = -40;

        const renderArrowhead = (tipX: number, tipY: number, angleDeg: number, color: string) => (
          <polygon
            points={`0,0 ${-ahLen},${-ahHalf} ${-ahLen},${ahHalf}`}
            fill={color}
            transform={`translate(${tipX},${tipY}) rotate(${angleDeg})`}
          />
        );

        const circumference = Math.PI * nodeR;
        const fillDurationSec = ((currentStepDuration - 100) / 1000).toFixed(2);

        const renderNode = (node: StepType, cx: number, cy: number) => {
          const cfg = STEP_CONFIG[node];
          const isNodeActive = activeType === node;
          const wasVisited = activeStep >= 0 && trace.slice(0, activeStep + 1).some((s) => s.type === node);
          return (
            <g key={node} style={{ transition: "opacity 0.3s ease" }} opacity={isNodeActive ? 1 : wasVisited ? 0.85 : 0.3}>
              <circle
                cx={cx} cy={cy} r={nodeR}
                fill={wasVisited && !isNodeActive ? cfg.color : "var(--code-background)"}
                stroke={cfg.color} strokeWidth={2 * S}
              />
              {isNodeActive && (
                <circle
                  key={`clock-${node}-${activeStep}`}
                  cx={cx} cy={cy}
                  r={nodeR / 2}
                  fill="none"
                  stroke={cfg.color}
                  strokeWidth={nodeR}
                  strokeDasharray={circumference}
                  strokeDashoffset={circumference}
                  transform={`rotate(-90 ${cx} ${cy})`}
                  style={{ animation: `clockFill ${fillDurationSec}s linear forwards` }}
                />
              )}
            </g>
          );
        };

        // Helper: pie slice path from 12 o'clock, filling clockwise
        const getPieSlicePath = (progress: number, cx: number, cy: number, r: number): string => {
          if (progress <= 0) return "";
          if (progress >= 100) return `M ${cx} ${cy} m -${r} 0 a ${r} ${r} 0 1 0 ${r * 2} 0 a ${r} ${r} 0 1 0 -${r * 2} 0`;
          const a = (progress / 100) * 2 * Math.PI;
          const startAngle = -Math.PI / 2;
          const endAngle = startAngle + a;
          const x1 = cx + r * Math.cos(startAngle);
          const y1 = cy + r * Math.sin(startAngle);
          const x2 = cx + r * Math.cos(endAngle);
          const y2 = cy + r * Math.sin(endAngle);
          const largeArcFlag = progress > 50 ? 1 : 0;
          return `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${largeArcFlag} 1 ${x2} ${y2} Z`;
        };

        return (
          <div
            style={{
              width: "100%",
              padding: "1rem",
              backgroundColor: "var(--code-background)",
              borderRadius: "8px",
              fontSize: "0.85rem",
              boxSizing: "border-box",
              marginBottom: "0.75rem",
            }}
          >
            {/* Slot for marquee or other header content */}
            {children}

            {/* 5-Node SVG Flow Diagram */}
            <div style={{ textAlign: "center", marginBottom: "0.75rem" }}>
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
                {forwardArrows.map(([fromIdx, toIdx]) => {
                  const x1 = mainXs[fromIdx] + nodeR + edgeGap;
                  const tipX = mainXs[toIdx] - nodeR - edgeGap;
                  const x2 = tipX - ahLen;
                  const active = isForwardArrowActive(fromIdx, toIdx);
                  const color = active ? STEP_CONFIG[mainNodes[toIdx]].color : "var(--text-color)";
                  return (
                    <g key={`fwd-${fromIdx}-${toIdx}`} opacity={active ? 1 : 0.2} style={{ transition: "opacity 0.3s ease" }}>
                      <line
                        x1={x1} y1={rowY} x2={x2} y2={rowY}
                        stroke={color} strokeWidth={active ? 2.5 * S : 2 * S}
                      />
                      {renderArrowhead(tipX, rowY, 0, color)}
                    </g>
                  );
                })}

                <g opacity={downActive ? 1 : loopVisible ? 0.35 : 0.15} style={{ transition: "opacity 0.3s ease" }}>
                  <path
                    d={downD} fill="none"
                    stroke={downActive ? "var(--color-green)" : "var(--text-color)"}
                    strokeWidth={downActive ? 2.5 * S : 2 * S}
                  />
                  {renderArrowhead(rTipX, rTipY, downArrowAngle, downActive ? "var(--color-green)" : "var(--text-color)")}
                </g>

                <g opacity={upActive ? 1 : loopVisible ? 0.35 : 0.15} style={{ transition: "opacity 0.3s ease" }}>
                  <path
                    d={upD} fill="none"
                    stroke={upActive ? "var(--color-blue)" : "var(--text-color)"}
                    strokeWidth={upActive ? 2.5 * S : 2 * S}
                  />
                  {renderArrowhead(lTipX, lTipY, upArrowAngle, upActive ? "var(--color-blue)" : "var(--text-color)")}
                </g>

                {loopCount > 0 && (
                  <g style={{ transition: "opacity 0.3s ease" }} opacity={loopVisible ? 1 : 0}>
                    <rect
                      x={badgeX - 14 * S} y={badgeY - 9 * S}
                      width={28 * S} height={18 * S} rx={4 * S}
                      fill="var(--code-background)" stroke="var(--color-green)" strokeWidth={1 * S} opacity={0.9}
                    />
                    <text
                      x={badgeX} y={badgeY}
                      textAnchor="middle" dominantBaseline="central" fontSize={11 * S} fontWeight={700}
                      fontFamily="var(--font-mono, monospace)" fill="var(--color-green)"
                    >
                      {`×${loopCount}`}
                    </text>
                  </g>
                )}

                {mainNodes.map((node, idx) => renderNode(node, mainXs[idx], rowY))}
                {renderNode("tools", toolsX, toolsY)}
              </svg>
            </div>

            {/* Progress bar — animated with spring */}
            <div
              style={{
                position: "relative",
                width: "100%",
                height: "24px",
                marginBottom: "0.75rem",
                borderRadius: "4px",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  height: "100%",
                  backgroundColor: "var(--background-color)",
                  borderRadius: "4px",
                }}
              />
              <animated.div
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  display: "flex",
                  width: "100%",
                  height: "100%",
                  clipPath: barSpring.progress.to((p) => `inset(0 ${100 - p}% 0 0)`),
                }}
              >
                {trace.map((step, idx) => {
                  const cfg = STEP_CONFIG[step.type];
                  return (
                    <div
                      key={`bar-${idx}-${step.type}`}
                      title={`${cfg.label}: ${step.durationMs ? formatMs(step.durationMs) : "—"} (${barWidths[idx].toFixed(1)}%)`}
                      style={{
                        width: `${barWidths[idx]}%`,
                        height: "100%",
                        backgroundColor: cfg.color,
                        flexShrink: 0,
                      }}
                    />
                  );
                })}
              </animated.div>
            </div>

            {/* Legend */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(140px, auto))",
                gap: "0.5rem 1.5rem",
                marginBottom: "0.75rem",
              }}
            >
              {legendEntries.map((entry, index) => {
                const prevComplete = index === 0 || legendEntries[index - 1].fillPercent >= 100;
                const isComplete = entry.fillPercent >= 100;
                const isActive = entry.fillPercent > 0 && entry.fillPercent < 100;
                const circleSize = 14;
                return (
                  <div
                    key={entry.type}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "0.5rem",
                      color: "var(--text-color)",
                      opacity: isComplete ? 1 : isActive ? 1 : prevComplete ? 0.6 : 0.3,
                      transition: "opacity 0.15s ease",
                    }}
                  >
                    <svg
                      width={circleSize}
                      height={circleSize}
                      viewBox="0 0 14 14"
                      style={{ flexShrink: 0 }}
                    >
                      <circle
                        cx="7"
                        cy="7"
                        r="6"
                        fill={entry.fillPercent > 0 ? entry.cfg.color : "none"}
                        stroke={entry.cfg.color}
                        strokeWidth="1.5"
                        opacity={entry.fillPercent > 0 ? 1 : 0.5}
                      />
                    </svg>
                    <span style={{ fontSize: "0.85rem", fontWeight: 500 }}>
                      {entry.cfg.label}
                    </span>
                  </div>
                );
              })}
            </div>

            {/* Answer + Receipt stack (inside card) */}
            {(() => {
              const synthesizeStep = trace.find((s) => s.type === "synthesize");
              const rawAnswer = synthesizeStep?.content ?? "";
              const answerText = rawAnswer
                .replace(/\n*#{0,3}\s*\*{0,2}Evidence\*{0,2}:?\**\s*[\s\S]*$/i, "")
                .replace(/\n*```(?:json)?\s*\[\s*[\s\S]*$/, "")
                .trim() || undefined;
              const allReceipts = synthesizeStep?.receipts ?? [];
              const uniqueReceipts: ReceiptEvidence[] = [];
              const seenIds = new Set<string>();
              for (const r of allReceipts) {
                if (!seenIds.has(r.imageId)) {
                  seenIds.add(r.imageId);
                  uniqueReceipts.push(r);
                }
              }

              const stackW = 200, stackH = 250, cW = 80, cH = 200, rBuf = 20;
              const mLeft = stackW - cW - rBuf;
              const mTop = Math.max(0, stackH - cH - rBuf);
              const sPositions = uniqueReceipts.map((r, i) => {
                let h = 0;
                for (let c = 0; c < r.imageId.length; c++) h = ((h << 5) - h + r.imageId.charCodeAt(c)) | 0;
                const p = (n: number) => ((Math.abs(h * (n + 1) * 2654435761) % 1000) / 1000);
                return {
                  rotation: p(1) * 24 - 12,
                  left: Math.max(rBuf / 2, Math.min(p(2) * mLeft, mLeft)),
                  top: Math.max(rBuf / 2, Math.min(p(3) * mTop, mTop)),
                  zIndex: i,
                };
              });

              return (
                <animated.div
                  style={{
                    ...answerHeightSpring,
                    overflow: "hidden",
                    display: "flex",
                    flexDirection: "column",
                    marginTop: "0.75rem",
                    marginBottom: "0.75rem",
                  }}
                >
                  {showAnswer && answerText ? (
                    <div
                      ref={answerRef}
                      style={{
                        display: "flex",
                        flexWrap: "wrap",
                        gap: "1rem",
                        width: "100%",
                        alignItems: "flex-start",
                      }}
                    >
                      <div
                        style={{
                          flex: "1 1 250px",
                          fontSize: "0.9rem",
                          color: "var(--text-color)",
                          minWidth: 0,
                        }}
                      >
                        <ReactMarkdown>{answerText}</ReactMarkdown>
                      </div>
                      {uniqueReceipts.length > 0 && (
                        <div
                          style={{
                            position: "relative",
                            width: `${stackW}px`,
                            height: `${stackH}px`,
                            flexShrink: 0,
                            margin: "0 auto",
                          }}
                        >
                          {uniqueReceipts.map((receipt, idx) => (
                            <div
                              key={receipt.imageId}
                              style={{
                                position: "absolute",
                                width: `${cW}px`,
                                left: `${sPositions[idx].left}px`,
                                top: `${sPositions[idx].top}px`,
                                "--r": `${sPositions[idx].rotation}deg`,
                                zIndex: sPositions[idx].zIndex,
                                border: "1px solid #ccc",
                                backgroundColor: "var(--background-color)",
                                boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
                                overflow: "hidden",
                                opacity: 0,
                                animation: `receiptFadeIn 0.4s ease-out ${idx * 80}ms forwards`,
                              } as React.CSSProperties}
                            >
                              <img
                                src={`${CDN_BASE}/${receipt.thumbnailKey}`}
                                alt={`${receipt.merchant} receipt`}
                                style={{ width: "100%", height: "auto", display: "block" }}
                                onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                              />
                            </div>
                          ))}
                          <style>{`
                            @keyframes receiptFadeIn {
                              from { opacity: 0; transform: rotate(var(--r, 0deg)) translateY(-20px); }
                              to   { opacity: 1; transform: rotate(var(--r, 0deg)) translateY(0); }
                            }
                          `}</style>
                        </div>
                      )}
                    </div>
                  ) : null}
                </animated.div>
              );
            })()}

          </div>
        );
      })()}

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
