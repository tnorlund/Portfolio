import { animated, useSprings } from "@react-spring/web";
import React from "react";
import { useViewportAnimation } from "../useDiagramOptimizations";
import { EMAIL_FUNNEL } from "./emailData";

const WIDTH = 520;
const ROW_H = 34;
const GAP = 8;
const LABEL_W = 190;
const VALUE_W = 92;
const BAR_MAX = WIDTH - LABEL_W - VALUE_W;

/* Ordinal ramp, one hue: light then dark steps (validated in the dataviz
 * reference palette). Widths use a log scale so the last stages stay
 * readable; the numbers carry the truth. */
const LIGHT_STEPS = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab"];
const DARK_STEPS = ["#6da7ec", "#5598e7", "#3987e5", "#256abf"];

const STYLE = `
.email-funnel { --f0:${LIGHT_STEPS[0]}; --f1:${LIGHT_STEPS[1]}; --f2:${LIGHT_STEPS[2]}; --f3:${LIGHT_STEPS[3]}; }
@media (prefers-color-scheme: dark) {
  .email-funnel { --f0:${DARK_STEPS[0]}; --f1:${DARK_STEPS[1]}; --f2:${DARK_STEPS[2]}; --f3:${DARK_STEPS[3]}; }
}
`;

const fmt = (n: number) => n.toLocaleString("en-US");

/**
 * 162,333 messages → 3,845 unique receipts. Log-scaled bar widths so the
 * bottom of the funnel is visible; direct labels carry the real counts.
 */
const EmailFunnel: React.FC<{ paused?: boolean }> = ({ paused = false }) => {
  const { containerRef, shouldAnimate } = useViewportAnimation(paused);
  const stages = EMAIL_FUNNEL;
  const maxLog = Math.log10(stages[0].count);
  const minLog = Math.log10(stages[stages.length - 1].count) - 0.35;

  const widths = stages.map((s) => {
    const t = (Math.log10(s.count) - minLog) / (maxLog - minLog);
    return Math.max(24, Math.round(BAR_MAX * Math.min(1, Math.max(0, t))));
  });

  const [springs] = useSprings(
    stages.length,
    (i) => ({
      from: { w: 0 },
      to: { w: shouldAnimate ? widths[i] : 0 },
      delay: i * 140,
      config: { tension: 170, friction: 26 },
    }),
    [shouldAnimate],
  );

  const height = stages.length * (ROW_H + GAP) - GAP + 4;

  return (
    <div
      ref={containerRef}
      className="email-funnel"
      style={{
        display: "flex",
        justifyContent: "center",
        marginTop: "1em",
        marginBottom: "1em",
      }}
    >
      <style>{STYLE}</style>
      <svg
        width={WIDTH}
        height={height}
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label="Funnel from 162,333 indexed messages down to 3,845 unique receipts"
        style={{ maxWidth: "100%", height: "auto" }}
        fontFamily="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
      >
        {stages.map((s, i) => {
          const y = i * (ROW_H + GAP) + 2;
          return (
            <g key={s.label}>
              <title>{`${s.label}: ${fmt(s.count)}`}</title>
              <text
                x={LABEL_W - 12}
                y={y + ROW_H / 2}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize="13"
                fill="var(--text-color)"
              >
                {s.label}
              </text>
              <animated.rect
                x={LABEL_W}
                y={y}
                height={ROW_H}
                width={springs[i].w}
                rx="4"
                fill={`var(--f${i})`}
              />
              <animated.text
                x={springs[i].w.to((w) => LABEL_W + w + 10)}
                y={y + ROW_H / 2}
                dominantBaseline="middle"
                fontSize="13"
                fontWeight={600}
                fill="var(--text-color)"
              >
                {fmt(s.count)}
              </animated.text>
            </g>
          );
        })}
      </svg>
    </div>
  );
};

export default EmailFunnel;
