import React from "react";
import { useViewportAnimation } from "../useDiagramOptimizations";
import { COVERAGE_BY_MONTH } from "./emailData";

const WIDTH = 560;
const HEIGHT = 330;
const PAD = { top: 20, right: 84, bottom: 70, left: 44 };
const PLOT_W = WIDTH - PAD.left - PAD.right;
const PLOT_H = HEIGHT - PAD.top - PAD.bottom;
const Y_MAX = 0.5;

/* Validated two-slot categorical palette (dataviz validator, both modes). */
const STYLE = `
.email-coverage { --cov-personal:#1E88E5; --cov-business:#FB8C00; --cov-grid:rgba(var(--text-color-rgb),0.12); }
@media (prefers-color-scheme: dark) {
  .email-coverage { --cov-personal:#3987e5; --cov-business:#d95926; }
}
.email-coverage table { border-collapse: collapse; font-size: 0.85rem; margin: 0.5rem auto; }
.email-coverage td, .email-coverage th { padding: 0.15rem 0.6rem; text-align: right; }
.email-coverage th:first-child, .email-coverage td:first-child { text-align: left; }
.email-coverage summary { cursor: pointer; font-size: 0.85rem; opacity: 0.75; text-align: center; }
`;

const SERIES = [
  { key: "personal" as const, label: "Personal", color: "var(--cov-personal)" },
  { key: "business" as const, label: "Business", color: "var(--cov-business)" },
];

const FONT =
  "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";

const monthLabel = (m: string) => {
  const [y, mo] = m.split("-").map(Number);
  const name = new Date(Date.UTC(y, mo - 1, 1)).toLocaleString("en-US", {
    month: "short",
    timeZone: "UTC",
  });
  return mo === 1 ? `${name} ${y}` : name;
};
const pct = (v: number) => `${Math.round(v * 100)}%`;

/**
 * Share of card purchases with a matched receipt, by month, personal vs
 * business card. Two series → legend + direct end labels; crosshair tooltip.
 */
const EmailCoverageChart: React.FC<{ paused?: boolean }> = ({ paused = false }) => {
  const { containerRef } = useViewportAnimation(paused);
  const data = COVERAGE_BY_MONTH;
  const [hover, setHover] = React.useState<number | null>(null);
  const svgRef = React.useRef<SVGSVGElement>(null);

  const x = (i: number) =>
    PAD.left + (data.length === 1 ? PLOT_W / 2 : (i / (data.length - 1)) * PLOT_W);
  const y = (v: number) => PAD.top + PLOT_H - (Math.min(v, Y_MAX) / Y_MAX) * PLOT_H;

  const linePath = (key: "personal" | "business") =>
    data.map((d, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(d[key])}`).join(" ");

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * WIDTH;
    const t = (px - PAD.left) / PLOT_W;
    const i = Math.round(t * (data.length - 1));
    setHover(Math.max(0, Math.min(data.length - 1, i)));
  };

  const gridTicks = [0, 0.1, 0.2, 0.3, 0.4, 0.5];
  const last = data.length - 1;
  // Keep the two end labels from colliding.
  const endP = y(data[last].personal);
  const endB = y(data[last].business);
  const endLabels = Math.abs(endP - endB) < 14
    ? { personal: Math.min(endP, endB) - 4, business: Math.max(endP, endB) + 10 }
    : { personal: endP, business: endB };

  return (
    <div
      ref={containerRef}
      className="email-coverage"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        marginTop: "1em",
        marginBottom: "1em",
      }}
    >
      <style>{STYLE}</style>
      <svg
        ref={svgRef}
        width={WIDTH}
        height={HEIGHT}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="Share of card purchases with a matched receipt, by month, personal versus business card"
        style={{ maxWidth: "100%", height: "auto", touchAction: "pan-y" }}
        fontFamily={FONT}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {/* Recessive grid + y labels */}
        {gridTicks.map((v) => (
          <g key={v}>
            <line
              x1={PAD.left}
              x2={PAD.left + PLOT_W}
              y1={y(v)}
              y2={y(v)}
              stroke="var(--cov-grid)"
              strokeWidth="1"
            />
            <text
              x={PAD.left - 8}
              y={y(v)}
              textAnchor="end"
              dominantBaseline="middle"
              fontSize="11"
              fontWeight={400}
              fill="var(--text-color)"
              opacity="0.7"
            >
              {pct(v)}
            </text>
          </g>
        ))}

        {/* x ticks every other month */}
        {data.map((d, i) =>
          i % 2 === 0 ? (
            <text
              key={d.month}
              x={x(i)}
              y={PAD.top + PLOT_H + 18}
              textAnchor="middle"
              fontSize="11"
              fontWeight={400}
              fill="var(--text-color)"
              opacity="0.7"
            >
              {monthLabel(d.month)}
            </text>
          ) : null,
        )}

        {/* Lines + markers */}
        {SERIES.map((s) => (
          <g key={s.key}>
            <path
              d={linePath(s.key)}
              fill="none"
              stroke={s.color}
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {data.map((d, i) => (
              <circle
                key={d.month}
                cx={x(i)}
                cy={y(d[s.key])}
                r={hover === i ? 5.5 : 4}
                fill={s.color}
                stroke="var(--background-color)"
                strokeWidth="2"
              />
            ))}
            <text
              x={PAD.left + PLOT_W + 10}
              y={endLabels[s.key]}
              dominantBaseline="middle"
              fontSize="12"
              fontWeight={600}
              fill="var(--text-color)"
            >
              {s.label}
            </text>
          </g>
        ))}

        {/* Crosshair + tooltip */}
        {hover !== null && (
          <g pointerEvents="none">
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1={PAD.top}
              y2={PAD.top + PLOT_H}
              stroke="var(--text-color)"
              strokeWidth="1"
              strokeDasharray="3 3"
              opacity="0.5"
            />
            {(() => {
              const d = data[hover];
              const boxW = 150;
              const boxH = 56;
              const bx = Math.min(
                Math.max(PAD.left, x(hover) - boxW / 2),
                PAD.left + PLOT_W - boxW,
              );
              const by = PAD.top + 2;
              return (
                <g transform={`translate(${bx},${by})`}>
                  <rect
                    width={boxW}
                    height={boxH}
                    rx="6"
                    fill="var(--code-background)"
                    stroke="var(--cov-grid)"
                  />
                  <text x="10" y="17" fontSize="12" fontWeight={600} fill="var(--text-color)">
                    {monthLabel(d.month)} {d.month.slice(0, 4)}
                  </text>
                  {SERIES.map((s, i) => (
                    <g key={s.key} transform={`translate(10,${33 + i * 15})`}>
                      <rect y="-7" width="10" height="10" rx="2" fill={s.color} />
                      <text x="16" fontSize="12" fill="var(--text-color)">
                        {s.label}: {pct(d[s.key])}
                      </text>
                    </g>
                  ))}
                </g>
              );
            })()}
          </g>
        )}

        {/* Legend */}
        <g transform={`translate(${PAD.left},${HEIGHT - 28})`} fontSize="11">
          {SERIES.map((s, i) => (
            <g key={s.key} transform={`translate(${i * 150},0)`}>
              <rect y="-9" width="12" height="12" rx="2" fill={s.color} />
              <text x="18" fill="var(--text-color)" fontWeight={400}>
                {s.label} card
              </text>
            </g>
          ))}
          <text
            y="20"
            fill="var(--text-color)"
            opacity="0.7"
            fontWeight={400}
          >
            Share of card purchases with a matched receipt, by month
          </text>
        </g>
      </svg>

      <details>
        <summary>Show as a table</summary>
        <table>
          <thead>
            <tr>
              <th scope="col">Month</th>
              <th scope="col">Personal</th>
              <th scope="col">Business</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.month}>
                <td>{d.month}</td>
                <td>{pct(d.personal)}</td>
                <td>{pct(d.business)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
};

export default EmailCoverageChart;
