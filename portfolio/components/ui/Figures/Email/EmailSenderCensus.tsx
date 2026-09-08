import { animated, useSprings } from "@react-spring/web";
import React from "react";
import { useViewportAnimation } from "../useDiagramOptimizations";
import { senderCensus } from "./emailData";

const WIDTH = 520;
const ROW_H = 22;
const GAP = 2; // surface gap between adjacent bars
const LABEL_W = 150;
const VALUE_W = 56;
const BAR_MAX = WIDTH - LABEL_W - VALUE_W;

const fmt = (n: number) => n.toLocaleString("en-US");

/**
 * Unique receipts per sender group — top 7 plus "Other". One hue, thin
 * bars, direct labels, hover highlight. Single series, so no legend.
 */
const EmailSenderCensus: React.FC<{ paused?: boolean }> = ({ paused = false }) => {
  const { containerRef, shouldAnimate } = useViewportAnimation(paused);
  const rows = React.useMemo(() => senderCensus(7), []);
  const max = Math.max(...rows.map((r) => r.receipts));
  const [hover, setHover] = React.useState<number | null>(null);

  const [springs] = useSprings(
    rows.length,
    (i) => ({
      from: { w: 0 },
      to: { w: shouldAnimate ? Math.round((rows[i].receipts / max) * BAR_MAX) : 0 },
      delay: i * 60,
      config: { tension: 170, friction: 26 },
    }),
    [shouldAnimate],
  );

  const height = rows.length * (ROW_H + GAP) - GAP + 4;

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
      <svg
        width={WIDTH}
        height={height}
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label="Unique email receipts by sender group"
        style={{ maxWidth: "100%", height: "auto" }}
        fontFamily="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
        onMouseLeave={() => setHover(null)}
      >
        {rows.map((r, i) => {
          const y = i * (ROW_H + GAP) + 2;
          const dim = hover !== null && hover !== i;
          const isOther = r.group === "other";
          return (
            <g
              key={r.group}
              onMouseEnter={() => setHover(i)}
              opacity={dim ? 0.45 : 1}
              style={{ transition: "opacity 120ms ease" }}
            >
              <title>{`${r.label}: ${fmt(r.receipts)} receipts`}</title>
              {/* Oversized hit target */}
              <rect x={0} y={y - GAP / 2} width={WIDTH} height={ROW_H + GAP} fill="transparent" />
              <text
                x={LABEL_W - 12}
                y={y + ROW_H / 2}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize="13"
                fill="var(--text-color)"
                opacity={isOther ? 0.7 : 1}
              >
                {r.label}
              </text>
              <animated.rect
                x={LABEL_W}
                y={y}
                height={ROW_H}
                width={springs[i].w}
                rx="4"
                fill={isOther ? "rgba(var(--text-color-rgb), 0.35)" : "var(--color-blue)"}
              />
              <animated.text
                x={springs[i].w.to((w) => LABEL_W + w + 8)}
                y={y + ROW_H / 2}
                dominantBaseline="middle"
                fontSize="12"
                fontWeight={600}
                fill="var(--text-color)"
              >
                {fmt(r.receipts)}
              </animated.text>
            </g>
          );
        })}
      </svg>
    </div>
  );
};

export default EmailSenderCensus;
