import { useEffect, useMemo, useState } from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  Constellation,
  LabelPositionStats,
  PatternResponse,
} from "../../../../types/api";
import styles from "./PatternDiscovery.module.css";

// Semantic label color palette — consistent with project conventions
const LABEL_COLORS: Record<string, string> = {
  MERCHANT_NAME: "var(--color-yellow)",
  ADDRESS_LINE: "var(--color-red)",
  PHONE_NUMBER: "var(--color-orange)",
  WEBSITE: "var(--color-purple)",
  DATE: "var(--color-blue)",
  TIME: "var(--color-blue)",
  GRAND_TOTAL: "var(--color-green)",
  SUBTOTAL: "var(--color-green)",
  TAX: "var(--color-green)",
  LINE_TOTAL: "var(--color-green)",
  PRODUCT_NAME: "var(--text-color)",
  QUANTITY: "var(--text-color)",
  UNIT_PRICE: "var(--text-color)",
  DISCOUNT: "var(--color-orange)",
  STORE_HOURS: "var(--color-orange)",
  PAYMENT_METHOD: "var(--color-purple)",
};

function labelColor(label: string): string {
  return LABEL_COLORS[label] ?? "var(--text-color)";
}

// ---------------------------------------------------------------------------
// Label Position Map (SVG) — left panel
// ---------------------------------------------------------------------------

const POS_SVG_WIDTH = 320;
const POS_SVG_HEIGHT = 400;
const POS_LEFT = 12;
const POS_RIGHT = POS_SVG_WIDTH - 12;
const POS_TOP = 24;
const POS_BOTTOM = POS_SVG_HEIGHT - 24;

function LabelPositionMap({
  positions,
}: {
  positions: Record<string, LabelPositionStats>;
}) {
  const entries = useMemo(() => {
    return Object.entries(positions)
      .filter(([, s]) => s.count > 0)
      .sort((a, b) => b[1].mean_y - a[1].mean_y); // top of receipt first
  }, [positions]);

  if (entries.length === 0) {
    return (
      <div className={styles.emptyPanel}>No label position data available</div>
    );
  }

  const maxCount = Math.max(...entries.map(([, s]) => s.count));

  // Map mean_y (0=bottom, 1=top) to SVG y (top=POS_TOP, bottom=POS_BOTTOM)
  const yScale = (meanY: number) =>
    POS_BOTTOM - (meanY * (POS_BOTTOM - POS_TOP));

  // Axis x position
  const axisX = POS_LEFT + 8;

  return (
    <svg
      viewBox={`0 0 ${POS_SVG_WIDTH} ${POS_SVG_HEIGHT}`}
      className={styles.svg}
      role="img"
      aria-label="Label positions on a normalized receipt"
    >
      {/* Thin vertical axis rule */}
      <line
        x1={axisX}
        y1={POS_TOP}
        x2={axisX}
        y2={POS_BOTTOM}
        stroke="var(--text-color)"
        strokeOpacity={0.15}
        strokeWidth={1}
      />

      {entries.map(([label, stats]) => {
        const cy = yScale(stats.mean_y);
        const stdPx = stats.std_y * (POS_BOTTOM - POS_TOP);
        const opacity = 0.35 + 0.65 * (stats.count / maxCount);

        return (
          <g key={label}>
            {/* Std deviation band */}
            {stdPx > 0.5 && (
              <rect
                x={axisX - 3}
                y={cy - stdPx}
                width={6}
                height={stdPx * 2}
                fill="var(--text-color)"
                fillOpacity={0.06}
                rx={2}
              />
            )}
            {/* Position mark */}
            <circle
              cx={axisX}
              cy={cy}
              r={3}
              fill={labelColor(label)}
              fillOpacity={opacity}
            />
            {/* Label text */}
            <text
              x={axisX + 12}
              y={cy}
              dy="0.35em"
              fill={labelColor(label)}
              fillOpacity={opacity}
              fontSize={11}
              fontFamily="var(--font-mono, monospace)"
            >
              {label}
            </text>
          </g>
        );
      })}

      {/* Y-axis annotations */}
      <text
        x={axisX}
        y={POS_TOP - 8}
        textAnchor="middle"
        fill="var(--text-color)"
        fillOpacity={0.3}
        fontSize={9}
      >
        top
      </text>
      <text
        x={axisX}
        y={POS_BOTTOM + 14}
        textAnchor="middle"
        fill="var(--text-color)"
        fillOpacity={0.3}
        fontSize={9}
      >
        bottom
      </text>
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Constellation Diagram (SVG) — right panel
// ---------------------------------------------------------------------------

const CON_SVG_WIDTH = 320;
const CON_GROUP_HEIGHT = 160;
const CON_PADDING = 20;

function ConstellationDiagram({
  constellations,
}: {
  constellations: Constellation[];
}) {
  if (constellations.length === 0) {
    return (
      <div className={styles.emptyPanel}>No constellation data available</div>
    );
  }

  const totalHeight =
    constellations.length * CON_GROUP_HEIGHT +
    (constellations.length - 1) * 12;

  return (
    <svg
      viewBox={`0 0 ${CON_SVG_WIDTH} ${totalHeight}`}
      className={styles.svg}
      role="img"
      aria-label="Constellation diagrams showing label co-occurrence geometry"
    >
      {constellations.map((c, idx) => (
        <ConstellationGroup
          key={idx}
          constellation={c}
          yOffset={idx * (CON_GROUP_HEIGHT + 12)}
        />
      ))}
    </svg>
  );
}

function ConstellationGroup({
  constellation,
  yOffset,
}: {
  constellation: Constellation;
  yOffset: number;
}) {
  const { labels, relative_positions, observation_count } = constellation;

  // Find the spatial extent to normalize positions into the drawing area
  const positions = Object.entries(relative_positions);
  if (positions.length === 0) return null;

  const dxs = positions.map(([, p]) => p.mean_dx);
  const dys = positions.map(([, p]) => p.mean_dy);
  const minDx = Math.min(...dxs);
  const maxDx = Math.max(...dxs);
  const minDy = Math.min(...dys);
  const maxDy = Math.max(...dys);

  const rangeX = maxDx - minDx || 0.1;
  const rangeY = maxDy - minDy || 0.1;

  // Map relative positions to SVG coordinates
  const drawW = CON_SVG_WIDTH - CON_PADDING * 2 - 80; // leave room for labels
  const drawH = CON_GROUP_HEIGHT - CON_PADDING * 2 - 20;
  const centerX = CON_SVG_WIDTH / 2;
  const centerY = yOffset + CON_GROUP_HEIGHT / 2;

  const scale = Math.min(drawW / rangeX, drawH / rangeY) * 0.8;

  const points: { label: string; cx: number; cy: number; stdR: number }[] =
    positions.map(([label, p]) => ({
      label,
      cx: centerX + p.mean_dx * scale,
      cy: centerY + p.mean_dy * scale, // positive dy = down
      stdR: Math.sqrt(p.std_dx ** 2 + p.std_dy ** 2) * scale,
    }));

  // Draw connecting lines between all labels in the constellation
  const lines: { x1: number; y1: number; x2: number; y2: number }[] = [];
  for (let i = 0; i < points.length; i++) {
    for (let j = i + 1; j < points.length; j++) {
      lines.push({
        x1: points[i].cx,
        y1: points[i].cy,
        x2: points[j].cx,
        y2: points[j].cy,
      });
    }
  }

  return (
    <g>
      {/* Connecting lines */}
      {lines.map((l, i) => (
        <line
          key={i}
          x1={l.x1}
          y1={l.y1}
          x2={l.x2}
          y2={l.y2}
          stroke="var(--text-color)"
          strokeOpacity={0.12}
          strokeWidth={1}
        />
      ))}

      {/* Std deviation ellipses + marks + labels */}
      {points.map((pt) => (
        <g key={pt.label}>
          {pt.stdR > 1 && (
            <circle
              cx={pt.cx}
              cy={pt.cy}
              r={Math.max(pt.stdR, 4)}
              fill={labelColor(pt.label)}
              fillOpacity={0.06}
              stroke={labelColor(pt.label)}
              strokeOpacity={0.1}
              strokeWidth={0.5}
            />
          )}
          <circle
            cx={pt.cx}
            cy={pt.cy}
            r={3.5}
            fill={labelColor(pt.label)}
            fillOpacity={0.8}
          />
          <text
            x={pt.cx + 7}
            y={pt.cy}
            dy="0.35em"
            fill={labelColor(pt.label)}
            fillOpacity={0.85}
            fontSize={10}
            fontFamily="var(--font-mono, monospace)"
          >
            {pt.label}
          </text>
        </g>
      ))}

      {/* Observation count annotation */}
      <text
        x={centerX}
        y={yOffset + CON_GROUP_HEIGHT - 4}
        textAnchor="middle"
        fill="var(--text-color)"
        fillOpacity={0.35}
        fontSize={9}
      >
        {observation_count} observations
      </text>
    </g>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export default function PatternDiscovery() {
  const [data, setData] = useState<PatternResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { ref, inView } = useInView({
    triggerOnce: true,
    threshold: 0.1,
    rootMargin: "100px",
  });

  useEffect(() => {
    if (!inView) return;
    api
      .fetchLabelEvaluatorPatterns()
      .then(setData)
      .catch((err) => setError(err.message));
  }, [inView]);

  if (error) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.error}>Failed to load patterns: {error}</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.loading}>Loading patterns...</div>
      </div>
    );
  }

  const { merchant } = data;

  return (
    <div ref={ref} className={styles.container}>
      {/* Header line */}
      <div className={styles.header}>
        <h3 className={styles.merchantName}>{merchant.merchant_name}</h3>
        <div className={styles.headerMeta}>
          {merchant.pattern && (
            <span className={styles.receiptType}>
              {merchant.pattern.receipt_type}
            </span>
          )}
          <span className={styles.receiptCount}>
            {merchant.receipt_count} receipts
          </span>
        </div>
      </div>

      {/* Two-panel layout */}
      <div className={styles.panels}>
        <div className={styles.panel}>
          <div className={styles.panelTitle}>Label Positions</div>
          <LabelPositionMap positions={merchant.label_positions} />
        </div>
        <div className={styles.panel}>
          <div className={styles.panelTitle}>Constellations</div>
          <ConstellationDiagram constellations={merchant.constellations} />
        </div>
      </div>

      {/* Annotation footer */}
      <div className={styles.footer}>
        {merchant.pattern?.receipt_type_reason && (
          <p className={styles.reason}>{merchant.pattern.receipt_type_reason}</p>
        )}
        <p className={styles.footerMeta}>
          1 of {data.total_count} merchants
        </p>
      </div>
    </div>
  );
}
