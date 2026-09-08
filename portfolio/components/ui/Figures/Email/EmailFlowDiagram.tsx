import React from "react";
import { useViewportAnimation } from "../useDiagramOptimizations";
import {
  BitStream,
  EmailGradients,
  LABEL_TEXT_PROPS,
  Phase,
  PathRef,
  delayFor,
  makeRefs,
  useCycle,
} from "./EmailBitStream";

/**
 * A row of 85×85 AWS-style tiles joined by the same five S-curve rails the
 * receipt page's CodeBuildDiagram uses (107.5px legs, ±1.64 / ±3.27 fan),
 * horizontal on desktop and vertical on phones. Each diagram on the email
 * page is a node list plus a storyboard of legs.
 */

export interface FlowNode {
  id: string;
  /** Draw the node centred on (x, y). */
  render: (x: number, y: number) => React.ReactNode;
  label?: string;
}

export interface FlowLeg {
  /** node indices; to < from runs the bits backwards along the rail */
  from: number;
  to: number;
  duration?: number;
  launch?: number;
  count?: number;
  chars?: string[];
}

interface EmailFlowDiagramProps {
  nodes: FlowNode[];
  legs: FlowLeg[];
  chars?: string[];
  paused?: boolean;
  ariaLabel: string;
}

const SPACING = 107.5;
const TILE = 85;
const PAD = 10;

/** Same breakpoint logic as CodeBuildDiagram: mobile during SSR. */
function useBreakpoint(breakpoint = 600): "mobile" | "desktop" {
  const [isMobile, setIsMobile] = React.useState(true);
  React.useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia(`(min-width: ${breakpoint}px)`);
    setIsMobile(!mq.matches);
    const handler = (e: MediaQueryListEvent) => setIsMobile(!e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [breakpoint]);
  return isMobile ? "mobile" : "desktop";
}

/**
 * The five rails between two tile centres, transcribed from the receipt
 * page's paths and scaled from its 107.5px leg. `axis` picks the layout.
 */
function Rails({
  refs,
  a,
  b,
  cross,
  axis,
}: {
  refs: PathRef[];
  a: number;
  b: number;
  cross: number;
  axis: "x" | "y";
}) {
  const L = b - a;
  const k = L / SPACING;
  const c = (o: number) =>
    // c dx1,dy1 dx2,dy2 dx3,dy3 s ... written in the receipt page's numbers
    axis === "x"
      ? `M${a},${cross}c${13.13 * k},0,${36.16 * k},${-o},${49.29 * k},${-o},${22.71 * k},0,${35.51 * k},${o},${58.21 * k},${o}`
      : `M${cross},${a}c0,${13.13 * k},${-o},${36.16 * k},${-o},${49.29 * k},0,${22.71 * k},${o},${35.51 * k},${o},${58.21 * k}`;
  const straight =
    axis === "x" ? `M${a},${cross}L${b},${cross}` : `M${cross},${a}L${cross},${b}`;
  const ds = [straight, c(1.64), c(-1.64), c(3.27), c(-3.27)];
  return (
    <g>
      {ds.map((d, i) => (
        <path key={i} d={d} fill="none" stroke="none" ref={refs[i]} />
      ))}
    </g>
  );
}

const EmailFlowDiagram: React.FC<EmailFlowDiagramProps> = ({
  nodes,
  legs,
  chars,
  paused = false,
  ariaLabel,
}) => {
  const { containerRef, shouldAnimate, springPause } =
    useViewportAnimation(paused);
  const layout = useBreakpoint(600);

  // One rail bundle per adjacent pair, keyed "i-j" with i < j.
  const railKeys = React.useMemo(
    () => nodes.slice(0, -1).map((_, i) => `${i}-${i + 1}`),
    [nodes],
  );
  const PATH_REFS = React.useMemo(
    () => Object.fromEntries(railKeys.map((k) => [k, makeRefs()])),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [railKeys, layout],
  );

  const TIMELINE = React.useMemo<Phase<string>[]>(
    () =>
      legs.map((leg) => {
        const lo = Math.min(leg.from, leg.to);
        return {
          paths: [`${lo}-${lo + 1}`],
          dir: leg.to > leg.from ? 1 : -1,
          duration: leg.duration,
          launch: leg.launch,
          count: leg.count,
          chars: leg.chars,
        };
      }),
    [legs],
  );
  const cycle = useCycle(TIMELINE, shouldAnimate);

  const n = nodes.length;
  const centers = nodes.map((_, i) => PAD + TILE / 2 + i * SPACING);
  const span = PAD * 2 + TILE + (n - 1) * SPACING;

  const bits = (
    <g key={cycle} fontFamily="monospace" fontSize="12">
      {TIMELINE.map((phase, phaseIdx) =>
        phase.paths.map((name) => (
          <BitStream
            key={`${layout}-${phaseIdx}-${name}`}
            pathRefs={PATH_REFS[name]}
            dir={phase.dir}
            duration={phase.duration}
            launch={phase.launch}
            initialDelay={delayFor(TIMELINE, phaseIdx)}
            count={phase.count}
            chars={phase.chars ?? chars}
            pause={springPause}
          />
        )),
      )}
    </g>
  );

  const desktop = (
    <svg
      width={span}
      height={200}
      viewBox={`0 0 ${span} 200`}
      role="img"
      aria-label={ariaLabel}
      style={{ maxWidth: "100%", height: "auto" }}
    >
      <EmailGradients />
      {nodes.map((node, i) => (
        <g key={node.id}>
          {node.render(centers[i], 100)}
          {node.label && (
            <text x={centers[i]} y={166} {...LABEL_TEXT_PROPS}>
              {node.label}
            </text>
          )}
        </g>
      ))}
      {railKeys.map((k, i) => (
        <Rails key={k} refs={PATH_REFS[k]} a={centers[i]} b={centers[i + 1]} cross={100} axis="x" />
      ))}
      {bits}
    </svg>
  );

  const mobile = (
    <svg
      width={300}
      height={span}
      viewBox={`0 0 300 ${span}`}
      role="img"
      aria-label={ariaLabel}
      style={{ maxWidth: "100%", height: "auto" }}
    >
      <EmailGradients />
      {nodes.map((node, i) => (
        <g key={node.id}>
          {node.render(150, centers[i])}
          {node.label && (
            <text
              x={150 + TILE / 2 + 14}
              y={centers[i]}
              {...LABEL_TEXT_PROPS}
              textAnchor="start"
              dominantBaseline="middle"
            >
              {node.label}
            </text>
          )}
        </g>
      ))}
      {railKeys.map((k, i) => (
        <Rails key={k} refs={PATH_REFS[k]} a={centers[i]} b={centers[i + 1]} cross={150} axis="y" />
      ))}
      {bits}
    </svg>
  );

  return (
    <div
      ref={containerRef}
      style={{
        display: "flex",
        justifyContent: "center",
        marginTop: "1em",
        marginBottom: "1em",
      }}
    >
      <div>{layout === "mobile" ? mobile : desktop}</div>
    </div>
  );
};

export default EmailFlowDiagram;
