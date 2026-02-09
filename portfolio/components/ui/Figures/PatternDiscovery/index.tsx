import React, { useEffect, useMemo, useState } from "react";
import { animated, useSprings } from "@react-spring/web";
import { api } from "../../../../services/api";
import {
  LabelEvaluatorWord,
  PatternEntry,
  PatternReceipt,
  PatternResponse,
} from "../../../../types/api";
import {
  detectImageFormatSupport,
  FormatSupport,
  getBestImageUrl,
} from "../../../../utils/imageFormat";
import styles from "./PatternDiscovery.module.css";

type SortKey = "merchant_name" | "receipt_type" | "total_issues" | "receipts";
type SortDir = "asc" | "desc";

const ISSUE_TYPE_COLORS: Record<string, string> = {
  missing_label_cluster: styles.barFillOrange,
  missing_constellation_member: styles.barFillBlue,
  text_label_conflict: styles.barFillRed,
};

const LABEL_COLORS: Record<string, string> = {
  MERCHANT_NAME: "var(--color-yellow)",
  DATE: "var(--color-blue)",
  TIME: "var(--color-blue)",
  AMOUNT: "var(--color-green)",
  LINE_TOTAL: "var(--color-green)",
  SUBTOTAL: "var(--color-green)",
  TAX: "var(--color-orange)",
  GRAND_TOTAL: "var(--color-red)",
  PRODUCT_NAME: "var(--color-purple)",
  ADDRESS: "var(--color-red)",
  WEBSITE: "var(--color-purple)",
  STORE_HOURS: "var(--color-orange)",
  PAYMENT_METHOD: "var(--color-orange)",
};

/** Max receipts to overlay for performance (~50 words each = ~250 rects). */
const MAX_OVERLAY_RECEIPTS = 5;

function formatIssueType(key: string): string {
  return key.replace(/_/g, " ");
}

export default function PatternDiscovery() {
  const [data, setData] = useState<PatternResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedMerchant, setExpandedMerchant] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("total_issues");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [formatSupport, setFormatSupport] = useState<FormatSupport | null>(null);

  useEffect(() => {
    api
      .fetchLabelEvaluatorPatterns(50)
      .then(setData)
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    detectImageFormatSupport().then(setFormatSupport);
  }, []);

  const sorted = useMemo(() => {
    if (!data) return [];
    const rows = [...data.receipts];
    rows.sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case "merchant_name":
          cmp = a.merchant_name.localeCompare(b.merchant_name);
          break;
        case "receipt_type":
          cmp = (a.pattern?.receipt_type ?? "").localeCompare(
            b.pattern?.receipt_type ?? ""
          );
          break;
        case "total_issues":
          cmp =
            a.geometric_summary.total_issues -
            b.geometric_summary.total_issues;
          break;
        case "receipts":
          cmp = a.trace_ids.length - b.trace_ids.length;
          break;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return rows;
  }, [data, sortKey, sortDir]);

  const summary = useMemo(() => {
    if (!data) return { total: 0, withPatterns: 0, totalIssues: 0 };
    const withPatterns = data.receipts.filter((r) => r.pattern !== null).length;
    const totalIssues = data.receipts.reduce(
      (sum, r) => sum + r.geometric_summary.total_issues,
      0
    );
    return { total: data.receipts.length, withPatterns, totalIssues };
  }, [data]);

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "merchant_name" ? "asc" : "desc");
    }
  }

  function toggleExpand(name: string) {
    setExpandedMerchant((prev) => (prev === name ? null : name));
  }

  const arrow = (key: SortKey) => {
    if (sortKey !== key) return "";
    return sortDir === "asc" ? " \u25B2" : " \u25BC";
  };

  if (error) {
    return (
      <div className={styles.container}>
        <div className={styles.error}>Failed to load patterns: {error}</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className={styles.container}>
        <div className={styles.loading}>Loading patterns...</div>
      </div>
    );
  }

  return (
    <div className={styles.container}>
      {/* Summary bar */}
      <div className={styles.summary}>
        <div className={styles.summaryItem}>
          <span className={styles.summaryValue}>{summary.total}</span>
          <span className={styles.summaryLabel}>Merchants</span>
        </div>
        <div className={styles.summaryItem}>
          <span className={styles.summaryValue}>{summary.withPatterns}</span>
          <span className={styles.summaryLabel}>With Patterns</span>
        </div>
        <div className={styles.summaryItem}>
          <span className={styles.summaryValue}>{summary.totalIssues}</span>
          <span className={styles.summaryLabel}>Total Issues</span>
        </div>
      </div>

      {/* Desktop table */}
      <table className={styles.table}>
        <thead>
          <tr>
            <th onClick={() => handleSort("merchant_name")}>
              Merchant{" "}
              <span className={styles.sortArrow}>
                {arrow("merchant_name")}
              </span>
            </th>
            <th onClick={() => handleSort("receipt_type")}>
              Receipt Type{" "}
              <span className={styles.sortArrow}>
                {arrow("receipt_type")}
              </span>
            </th>
            <th onClick={() => handleSort("total_issues")}>
              Issues{" "}
              <span className={styles.sortArrow}>
                {arrow("total_issues")}
              </span>
            </th>
            <th onClick={() => handleSort("receipts")}>
              Receipts{" "}
              <span className={styles.sortArrow}>{arrow("receipts")}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((entry) => (
            <React.Fragment key={entry.merchant_name}>
              <tr
                className={styles.merchantRow}
                onClick={() => toggleExpand(entry.merchant_name)}
              >
                <td className={styles.merchantName}>
                  {entry.merchant_name}
                </td>
                <td>
                  {entry.pattern ? (
                    <span className={styles.receiptType}>
                      {entry.pattern.receipt_type}
                    </span>
                  ) : (
                    <span className={styles.noPattern}>--</span>
                  )}
                </td>
                <td
                  className={`${styles.issueCount} ${
                    entry.geometric_summary.total_issues === 0
                      ? styles.zeroIssues
                      : ""
                  }`}
                >
                  {entry.geometric_summary.total_issues}
                </td>
                <td>{entry.trace_ids.length}</td>
              </tr>
              <tr className={styles.detailRow}>
                <td colSpan={4}>
                  <div
                    className={`${styles.detailContent} ${
                      expandedMerchant === entry.merchant_name
                        ? styles.detailContentOpen
                        : ""
                    }`}
                  >
                    <ExpandedDetail entry={entry} formatSupport={formatSupport} />
                  </div>
                </td>
              </tr>
            </React.Fragment>
          ))}
        </tbody>
      </table>

      {/* Mobile card list */}
      <div className={styles.cardList}>
        {sorted.map((entry) => (
          <div key={entry.merchant_name}>
            <div
              className={styles.card}
              onClick={() => toggleExpand(entry.merchant_name)}
            >
              <div className={styles.cardHeader}>
                <span className={styles.cardMerchant}>
                  {entry.merchant_name}
                </span>
                <span className={styles.cardBadge}>
                  {entry.pattern?.receipt_type ?? "--"}
                </span>
              </div>
              <div className={styles.cardStats}>
                <span>{entry.geometric_summary.total_issues} issues</span>
                <span>{entry.trace_ids.length} receipts</span>
              </div>
            </div>
            <div
              className={`${styles.detailContent} ${
                expandedMerchant === entry.merchant_name
                  ? styles.detailContentOpen
                  : ""
              }`}
            >
              <ExpandedDetail entry={entry} formatSupport={formatSupport} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pattern Overlay — animated word-rect stacking
// ---------------------------------------------------------------------------

/** Check whether any receipt in the list carries word data. */
function hasWordData(receipts: PatternReceipt[]): boolean {
  return receipts.some((r) => r.words && r.words.length > 0);
}

/** Collect unique label names present across all receipt words. */
function collectLabels(receipts: PatternReceipt[]): string[] {
  const labels = new Set<string>();
  for (const r of receipts) {
    for (const w of r.words ?? []) {
      if (w.label) labels.add(w.label);
    }
  }
  return Array.from(labels).sort();
}

function PatternOverlay({ receipts }: { receipts: PatternReceipt[] }) {
  const overlayReceipts = useMemo(
    () => receipts.filter((r) => r.words && r.words.length > 0).slice(0, MAX_OVERLAY_RECEIPTS),
    [receipts],
  );
  const count = overlayReceipts.length;

  // Per-receipt opacity springs
  const [springs, springApi] = useSprings(count, () => ({ opacity: 0 }), [count]);

  // Timer-based animation loop: build-up → hold → reset → repeat
  useEffect(() => {
    if (count === 0) return;
    const timers: ReturnType<typeof setTimeout>[] = [];
    let cancelled = false;

    function runCycle() {
      if (cancelled) return;
      // Phase 1: Build-up — stagger receipts in over ~4s
      const stagger = 4000 / count;
      for (let i = 0; i < count; i++) {
        timers.push(
          setTimeout(() => {
            if (cancelled) return;
            springApi.start((idx) => {
              if (idx !== i) return {};
              return { opacity: 0.4, config: { duration: 600 } };
            });
          }, i * stagger),
        );
      }
      // Phase 2: Hold for 3s (starts at 4s)
      // Phase 3: Reset — fade out (starts at 7s)
      timers.push(
        setTimeout(() => {
          if (cancelled) return;
          springApi.start(() => ({ opacity: 0, config: { duration: 600 } }));
        }, 7000),
      );
      // Restart cycle at 8s
      timers.push(
        setTimeout(() => {
          if (cancelled) return;
          runCycle();
        }, 8000),
      );
    }

    runCycle();
    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
  }, [count, springApi]);

  // Use a fixed normalized SVG space (width=500, height=400 matches 5:4 ratio)
  const svgWidth = 500;
  const svgHeight = 400;

  // Determine the best reference dimensions (use first receipt with w/h)
  const refReceipt = overlayReceipts.find((r) => r.width > 0 && r.height > 0);
  const refW = refReceipt?.width ?? 1;
  const refH = refReceipt?.height ?? 1;

  return (
    <div className={styles.patternOverlay}>
      <svg viewBox={`0 0 ${svgWidth} ${svgHeight}`} width="100%" height="100%">
        {springs.map((style, i) => {
          const receipt = overlayReceipts[i];
          if (!receipt?.words) return null;
          // Scale factor to map receipt pixels → SVG space
          const scaleX = svgWidth / refW;
          const scaleY = svgHeight / refH;
          return (
            <animated.g key={`${receipt.image_id}-${receipt.receipt_id}`} opacity={style.opacity}>
              {receipt.words.map((word: LabelEvaluatorWord, j: number) => {
                const x = word.bbox.x * refW * scaleX;
                const y = (1 - word.bbox.y - word.bbox.height) * refH * scaleY;
                const w = word.bbox.width * refW * scaleX;
                const h = word.bbox.height * refH * scaleY;
                return (
                  <rect
                    key={`${word.line_id}-${word.word_id}-${j}`}
                    x={x}
                    y={y}
                    width={w}
                    height={h}
                    fill={LABEL_COLORS[word.label ?? ""] ?? "var(--color-gray, #888)"}
                    rx={2}
                  />
                );
              })}
            </animated.g>
          );
        })}
      </svg>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Label legend
// ---------------------------------------------------------------------------

function LabelLegend({ labels }: { labels: string[] }) {
  if (labels.length === 0) return null;
  return (
    <div className={styles.labelLegend}>
      {labels.map((label) => (
        <span key={label} className={styles.legendItem}>
          <span
            className={styles.legendDot}
            style={{ background: LABEL_COLORS[label] ?? "var(--color-gray, #888)" }}
          />
          {label.replace(/_/g, " ").toLowerCase()}
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ExpandedDetail — shows overlay when words available, otherwise falls back
// ---------------------------------------------------------------------------

function ExpandedDetail({
  entry,
  formatSupport,
}: {
  entry: PatternEntry;
  formatSupport: FormatSupport | null;
}) {
  const { pattern, geometric_summary, receipts = [] } = entry;

  const showOverlay = hasWordData(receipts);
  const presentLabels = useMemo(() => collectLabels(receipts), [receipts]);

  // Count total geometric issues across receipts
  const totalReceiptIssues = useMemo(
    () =>
      receipts.reduce(
        (sum, r) => sum + (r.geometric_issues?.length ?? 0),
        0,
      ),
    [receipts],
  );

  const validReceipts = receipts
    .filter((r) => r.cdn_s3_key !== null)
    .map((r) => ({
      receipt_id: r.receipt_id,
      cdn_s3_key: r.cdn_s3_key!,
      cdn_webp_s3_key: r.cdn_webp_s3_key ?? undefined,
      cdn_avif_s3_key: r.cdn_avif_s3_key ?? undefined,
      cdn_medium_s3_key: r.cdn_medium_s3_key ?? undefined,
      cdn_medium_webp_s3_key: r.cdn_medium_webp_s3_key ?? undefined,
      cdn_medium_avif_s3_key: r.cdn_medium_avif_s3_key ?? undefined,
    }));

  // ---- Fallback: original bar-chart view (no word data) ----
  if (!showOverlay) {
    const issueMax = Math.max(
      1,
      ...Object.values(geometric_summary.issue_types),
    );
    const labelMax = Math.max(
      1,
      ...Object.values(geometric_summary.top_suggested_labels),
    );
    return (
      <>
        <div className={styles.detailInner}>
          <div className={styles.detailSection}>
            <div className={styles.detailSectionTitle}>Pattern</div>
            {pattern === null ? (
              <div className={styles.noPatternMessage}>No pattern discovered</div>
            ) : (
              <>
                <div className={styles.reason}>{pattern.receipt_type_reason}</div>
                <div className={styles.detailGrid}>
                  <span className={styles.detailKey}>Structure</span>
                  <span className={styles.detailValue}>
                    {pattern.item_structure ?? "--"}
                  </span>
                  {pattern.lines_per_item && (
                    <>
                      <span className={styles.detailKey}>Lines/Item</span>
                      <span className={styles.detailValue}>
                        {pattern.lines_per_item.typical} (
                        {pattern.lines_per_item.min}-{pattern.lines_per_item.max})
                      </span>
                    </>
                  )}
                  {pattern.barcode_pattern && (
                    <>
                      <span className={styles.detailKey}>Barcode</span>
                      <span className={styles.detailValue}>
                        <code className={styles.mono}>{pattern.barcode_pattern}</code>
                      </span>
                    </>
                  )}
                </div>
                {pattern.special_markers && pattern.special_markers.length > 0 && (
                  <>
                    <div className={styles.detailSectionTitle} style={{ marginTop: "0.75rem" }}>
                      Special Markers
                    </div>
                    <div className={styles.markers}>
                      {pattern.special_markers.map((m, i) => (
                        <code key={i} className={styles.mono}>{m}</code>
                      ))}
                    </div>
                  </>
                )}
                {pattern.label_positions && (
                  <>
                    <div className={styles.detailSectionTitle} style={{ marginTop: "0.75rem" }}>
                      Label Positions
                    </div>
                    <div className={styles.labelPositions}>
                      {Object.entries(pattern.label_positions).map(([label, pos]) => (
                        <React.Fragment key={label}>
                          <span className={styles.labelName}>{label}</span>
                          <span
                            className={`${styles.labelPosition} ${
                              pos === "not_found" ? styles.labelPositionNotFound : ""
                            }`}
                          >
                            {pos}
                          </span>
                        </React.Fragment>
                      ))}
                    </div>
                  </>
                )}
                {pattern.grouping_rule && (
                  <>
                    <div className={styles.detailSectionTitle} style={{ marginTop: "0.75rem" }}>
                      Grouping Rule
                    </div>
                    <div className={styles.groupingRule}>{pattern.grouping_rule}</div>
                  </>
                )}
              </>
            )}
          </div>
          <div className={styles.detailSection}>
            <div className={styles.detailSectionTitle}>
              Geometric Issues ({geometric_summary.total_issues})
            </div>
            {Object.keys(geometric_summary.issue_types).length === 0 ? (
              <div className={styles.emptyBars}>No issues found</div>
            ) : (
              <div className={styles.barChart}>
                {Object.entries(geometric_summary.issue_types).map(([type, count]) => (
                  <div key={type} className={styles.barRow}>
                    <span className={styles.barLabel}>{formatIssueType(type)}</span>
                    <div className={styles.barTrack}>
                      <div
                        className={`${styles.barFill} ${ISSUE_TYPE_COLORS[type] ?? styles.barFillPurple}`}
                        style={{ width: `${(count / issueMax) * 100}%` }}
                      />
                    </div>
                    <span className={styles.barCount}>{count}</span>
                  </div>
                ))}
              </div>
            )}
            <div className={styles.detailSectionTitle} style={{ marginTop: "1rem" }}>
              Suggested Labels
            </div>
            {Object.keys(geometric_summary.top_suggested_labels).length === 0 ? (
              <div className={styles.emptyBars}>None</div>
            ) : (
              <div className={styles.barChart}>
                {Object.entries(geometric_summary.top_suggested_labels).map(([label, count]) => (
                  <div key={label} className={styles.barRow}>
                    <span className={styles.barLabel}>{label}</span>
                    <div className={styles.barTrack}>
                      <div
                        className={`${styles.barFill} ${styles.barFillPurple}`}
                        style={{ width: `${(count / labelMax) * 100}%` }}
                      />
                    </div>
                    <span className={styles.barCount}>{count}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className={styles.receiptsSection}>
          <div className={styles.detailSectionTitle}>
            Receipt Images ({validReceipts.length})
          </div>
          {validReceipts.length === 0 ? (
            <div className={styles.receiptThumbEmpty}>No receipt images</div>
          ) : formatSupport === null ? null : (
            <div className={styles.receiptThumbnails}>
              {validReceipts.map((r) => (
                <img
                  key={r.receipt_id}
                  className={styles.receiptThumb}
                  src={getBestImageUrl(r, formatSupport, "medium")}
                  alt={`Receipt ${r.receipt_id}`}
                  loading="lazy"
                />
              ))}
            </div>
          )}
        </div>
      </>
    );
  }

  // ---- Primary: animated overlay view ----
  return (
    <>
      <div style={{ padding: "1rem 0.75rem 0" }}>
        <PatternOverlay receipts={receipts} />

        {/* Condensed pattern info */}
        <div className={styles.patternInfoRow}>
          {pattern && <span>Type: {pattern.receipt_type}</span>}
          <span>{receipts.length} receipts</span>
          {totalReceiptIssues > 0 && (
            <span>
              {totalReceiptIssues} issue{totalReceiptIssues !== 1 ? "s" : ""}
            </span>
          )}
        </div>

        <LabelLegend labels={presentLabels} />
      </div>

      <div className={styles.receiptsSection}>
        <div className={styles.detailSectionTitle}>
          Receipt Images ({validReceipts.length})
        </div>
        {validReceipts.length === 0 ? (
          <div className={styles.receiptThumbEmpty}>No receipt images</div>
        ) : formatSupport === null ? null : (
          <div className={styles.receiptThumbnails}>
            {validReceipts.map((r) => (
              <img
                key={r.receipt_id}
                className={styles.receiptThumb}
                src={getBestImageUrl(r, formatSupport, "medium")}
                alt={`Receipt ${r.receipt_id}`}
                loading="lazy"
              />
            ))}
          </div>
        )}
      </div>
    </>
  );
}
