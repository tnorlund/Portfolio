import React from "react";
import { useSpring, useSprings, animated, config } from "@react-spring/web";

interface QAAgentFlowProps {
  /** Currently active node (for highlighting) */
  activeNode?: "plan" | "agent" | "tools" | "shape" | "synthesize" | null;
  /** Whether to animate the flow */
  animate?: boolean;
}

interface NodeConfig {
  id: string;
  label: string;
  description: string;
  x: number;
  y: number;
  color: string;
}

const NODES: NodeConfig[] = [
  {
    id: "plan",
    label: "Plan",
    description: "Classify question & determine retrieval strategy",
    x: 150,
    y: 40,
    color: "var(--color-purple)",
  },
  {
    id: "agent",
    label: "Agent",
    description: "ReAct loop - decide next action",
    x: 150,
    y: 120,
    color: "var(--color-blue)",
  },
  {
    id: "tools",
    label: "Tools",
    description: "Execute search, get receipts, aggregate",
    x: 280,
    y: 120,
    color: "var(--color-green)",
  },
  {
    id: "shape",
    label: "Shape",
    description: "Dedupe, filter, limit context",
    x: 150,
    y: 200,
    color: "var(--color-orange)",
  },
  {
    id: "synthesize",
    label: "Synthesize",
    description: "Generate final answer with evidence",
    x: 150,
    y: 280,
    color: "var(--color-red)",
  },
];

// Edge definitions: [from, to, type]
type EdgeType = "normal" | "loop" | "conditional";
const EDGES: [string, string, EdgeType][] = [
  ["plan", "agent", "normal"],
  ["agent", "tools", "conditional"],
  ["tools", "agent", "loop"],
  ["agent", "shape", "conditional"],
  ["tools", "shape", "conditional"],
  ["shape", "synthesize", "normal"],
  ["shape", "agent", "loop"], // retry path
];

const QAAgentFlow: React.FC<QAAgentFlowProps> = ({
  activeNode = null,
  animate = true,
}) => {
  const [hoveredNode, setHoveredNode] = React.useState<string | null>(null);
  const [animationPhase, setAnimationPhase] = React.useState(0);

  // Animation cycle through nodes
  React.useEffect(() => {
    if (!animate) return;

    const phases = ["plan", "agent", "tools", "agent", "tools", "agent", "shape", "synthesize"];
    const interval = setInterval(() => {
      setAnimationPhase((p) => (p + 1) % phases.length);
    }, 800);

    return () => clearInterval(interval);
  }, [animate]);

  const animatedNode = animate
    ? ["plan", "agent", "tools", "agent", "tools", "agent", "shape", "synthesize"][animationPhase]
    : activeNode;

  // Node hover spring
  const nodeSpring = useSpring({
    scale: hoveredNode ? 1.1 : 1,
    config: config.wobbly,
  });

  // Get node position by id
  const getNode = (id: string) => NODES.find((n) => n.id === id);

  // Draw edge path
  const getEdgePath = (from: string, to: string, type: EdgeType): string => {
    const fromNode = getNode(from);
    const toNode = getNode(to);
    if (!fromNode || !toNode) return "";

    const fx = fromNode.x;
    const fy = fromNode.y;
    const tx = toNode.x;
    const ty = toNode.y;

    if (type === "loop" && from === "tools" && to === "agent") {
      // Curved loop back from tools to agent
      return `M ${fx} ${fy} C ${fx + 40} ${fy - 60}, ${tx + 40} ${ty - 60}, ${tx + 35} ${ty}`;
    }

    if (type === "loop" && from === "shape" && to === "agent") {
      // Retry loop from shape back to agent (left side)
      return `M ${fx - 35} ${fy} C ${fx - 80} ${fy}, ${tx - 80} ${ty}, ${tx - 35} ${ty}`;
    }

    // Straight or slight curve
    const midY = (fy + ty) / 2;
    if (fx === tx) {
      // Vertical line
      return `M ${fx} ${fy + 25} L ${tx} ${ty - 25}`;
    }

    // Horizontal or diagonal
    return `M ${fx + 35} ${fy} C ${fx + 50} ${midY}, ${tx - 50} ${midY}, ${tx - 35} ${ty}`;
  };

  // Pulse animation for active edges
  const [edgeSprings] = useSprings(EDGES.length, (i) => ({
    strokeDashoffset: animate ? [100, 0] : [0, 0],
    config: { duration: 1500 },
    loop: animate,
  }));

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "1em",
        fontFamily: "var(--font-sans, system-ui, sans-serif)",
      }}
    >
      <svg
        width="360"
        height="340"
        viewBox="0 0 360 340"
        style={{ maxWidth: "100%" }}
      >
        {/* Background */}
        <rect
          x="0"
          y="0"
          width="360"
          height="340"
          fill="var(--background-color)"
          rx="8"
        />

        {/* START marker */}
        <g>
          <circle cx="60" cy="40" r="12" fill="var(--color-green)" />
          <text
            x="60"
            y="44"
            textAnchor="middle"
            fill="white"
            fontSize="10"
            fontWeight="bold"
          >
            S
          </text>
          <path
            d="M 72 40 L 115 40"
            stroke="var(--color-green)"
            strokeWidth="2"
            fill="none"
            markerEnd="url(#arrowhead)"
          />
        </g>

        {/* END marker */}
        <g>
          <circle cx="60" cy="280" r="12" fill="var(--color-red)" />
          <text
            x="60"
            y="284"
            textAnchor="middle"
            fill="white"
            fontSize="10"
            fontWeight="bold"
          >
            E
          </text>
          <path
            d="M 115 280 L 72 280"
            stroke="var(--color-red)"
            strokeWidth="2"
            fill="none"
            markerEnd="url(#arrowhead-red)"
          />
        </g>

        {/* Arrow marker definitions */}
        <defs>
          <marker
            id="arrowhead"
            markerWidth="10"
            markerHeight="7"
            refX="9"
            refY="3.5"
            orient="auto"
          >
            <polygon
              points="0 0, 10 3.5, 0 7"
              fill="var(--text-color)"
              opacity="0.6"
            />
          </marker>
          <marker
            id="arrowhead-red"
            markerWidth="10"
            markerHeight="7"
            refX="9"
            refY="3.5"
            orient="auto"
          >
            <polygon points="0 0, 10 3.5, 0 7" fill="var(--color-red)" />
          </marker>
          <marker
            id="arrowhead-active"
            markerWidth="10"
            markerHeight="7"
            refX="9"
            refY="3.5"
            orient="auto"
          >
            <polygon points="0 0, 10 3.5, 0 7" fill="var(--color-blue)" />
          </marker>
        </defs>

        {/* Edges */}
        {EDGES.map(([from, to, type], i) => {
          const path = getEdgePath(from, to, type);
          const isActive =
            animatedNode === from ||
            (animatedNode === to && from === "tools");
          const isLoop = type === "loop";

          return (
            <g key={`${from}-${to}`}>
              {/* Background path */}
              <path
                d={path}
                stroke="var(--text-color)"
                strokeWidth={isActive ? 2.5 : 1.5}
                strokeOpacity={isActive ? 0.8 : 0.3}
                fill="none"
                strokeDasharray={isLoop ? "5,5" : "none"}
                markerEnd={isActive ? "url(#arrowhead-active)" : "url(#arrowhead)"}
              />
            </g>
          );
        })}

        {/* Nodes */}
        {NODES.map((node) => {
          const isActive = animatedNode === node.id;
          const isHovered = hoveredNode === node.id;

          return (
            <g
              key={node.id}
              onMouseEnter={() => setHoveredNode(node.id)}
              onMouseLeave={() => setHoveredNode(null)}
              style={{ cursor: "pointer" }}
            >
              {/* Node background glow */}
              {(isActive || isHovered) && (
                <rect
                  x={node.x - 40}
                  y={node.y - 18}
                  width="80"
                  height="36"
                  rx="8"
                  fill={node.color}
                  opacity="0.2"
                />
              )}

              {/* Node box */}
              <rect
                x={node.x - 35}
                y={node.y - 15}
                width="70"
                height="30"
                rx="6"
                fill={isActive || isHovered ? node.color : "var(--code-background)"}
                stroke={node.color}
                strokeWidth={isActive ? 3 : 2}
              />

              {/* Node label */}
              <text
                x={node.x}
                y={node.y + 5}
                textAnchor="middle"
                fill={isActive || isHovered ? "white" : "var(--text-color)"}
                fontSize="12"
                fontWeight="600"
                fontFamily="monospace"
              >
                {node.label}
              </text>
            </g>
          );
        })}

        {/* Loop labels */}
        <text
          x="300"
          y="75"
          textAnchor="middle"
          fill="var(--text-color)"
          fontSize="9"
          opacity="0.6"
        >
          tool calls
        </text>
        <text
          x="45"
          y="160"
          textAnchor="middle"
          fill="var(--text-color)"
          fontSize="9"
          opacity="0.6"
        >
          retry
        </text>
      </svg>

      {/* Description panel */}
      <div
        style={{
          marginTop: "1em",
          padding: "0.75em 1em",
          backgroundColor: "var(--code-background)",
          borderRadius: "6px",
          minHeight: "3em",
          width: "100%",
          maxWidth: "340px",
          textAlign: "center",
        }}
      >
        {hoveredNode ? (
          <>
            <div
              style={{
                fontWeight: 600,
                color: getNode(hoveredNode)?.color,
                marginBottom: "0.25em",
              }}
            >
              {getNode(hoveredNode)?.label}
            </div>
            <div
              style={{
                fontSize: "0.85em",
                color: "var(--text-color)",
                opacity: 0.8,
              }}
            >
              {getNode(hoveredNode)?.description}
            </div>
          </>
        ) : (
          <div
            style={{
              fontSize: "0.85em",
              color: "var(--text-color)",
              opacity: 0.6,
            }}
          >
            Hover over a node to see details
          </div>
        )}
      </div>
    </div>
  );
};

export default QAAgentFlow;
