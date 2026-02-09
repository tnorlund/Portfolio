import React, { useEffect, useMemo, useState } from "react";
import { api } from "../../../../services/api";
import { PatternEntry, PatternResponse } from "../../../../types/api";
import styles from "./PatternDiscovery.module.css";

type SortKey = "merchant_name" | "receipt_type" | "total_issues" | "receipts";
type SortDir = "asc" | "desc";

const ISSUE_TYPE_COLORS: Record<string, string> = {
  missing_label_cluster: styles.barFillOrange,
  missing_constellation_member: styles.barFillBlue,
  text_label_conflict: styles.barFillRed,
};

function formatIssueType(key: string): string {
  return key.replace(/_/g, " ");
}

export default function PatternDiscovery() {
  const [data, setData] = useState<PatternResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedMerchant, setExpandedMerchant] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("total_issues");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  useEffect(() => {
    api
      .fetchLabelEvaluatorPatterns(50)
      .then(setData)
      .catch((err) => setError(err.message));
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
                    <ExpandedDetail entry={entry} />
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
              <ExpandedDetail entry={entry} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ExpandedDetail({ entry }: { entry: PatternEntry }) {
  const { pattern, geometric_summary } = entry;
  const issueMax = Math.max(
    1,
    ...Object.values(geometric_summary.issue_types)
  );
  const labelMax = Math.max(
    1,
    ...Object.values(geometric_summary.top_suggested_labels)
  );

  return (
    <div className={styles.detailInner}>
      {/* Left: Pattern details */}
      <div className={styles.detailSection}>
        <div className={styles.detailSectionTitle}>Pattern</div>
        {pattern === null ? (
          <div className={styles.noPatternMessage}>
            No pattern discovered
          </div>
        ) : (
          <>
            <div className={styles.reason}>
              {pattern.receipt_type_reason}
            </div>
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
                    <code className={styles.mono}>
                      {pattern.barcode_pattern}
                    </code>
                  </span>
                </>
              )}
            </div>

            {pattern.special_markers &&
              pattern.special_markers.length > 0 && (
                <>
                  <div
                    className={styles.detailSectionTitle}
                    style={{ marginTop: "0.75rem" }}
                  >
                    Special Markers
                  </div>
                  <div className={styles.markers}>
                    {pattern.special_markers.map((m, i) => (
                      <code key={i} className={styles.mono}>
                        {m}
                      </code>
                    ))}
                  </div>
                </>
              )}

            {pattern.label_positions && (
              <>
                <div
                  className={styles.detailSectionTitle}
                  style={{ marginTop: "0.75rem" }}
                >
                  Label Positions
                </div>
                <div className={styles.labelPositions}>
                  {Object.entries(pattern.label_positions).map(
                    ([label, pos]) => (
                      <React.Fragment key={label}>
                        <span className={styles.labelName}>{label}</span>
                        <span
                          className={`${styles.labelPosition} ${
                            pos === "not_found"
                              ? styles.labelPositionNotFound
                              : ""
                          }`}
                        >
                          {pos}
                        </span>
                      </React.Fragment>
                    )
                  )}
                </div>
              </>
            )}

            {pattern.grouping_rule && (
              <>
                <div
                  className={styles.detailSectionTitle}
                  style={{ marginTop: "0.75rem" }}
                >
                  Grouping Rule
                </div>
                <div className={styles.groupingRule}>
                  {pattern.grouping_rule}
                </div>
              </>
            )}
          </>
        )}
      </div>

      {/* Right: Geometric summary */}
      <div className={styles.detailSection}>
        <div className={styles.detailSectionTitle}>
          Geometric Issues ({geometric_summary.total_issues})
        </div>
        {Object.keys(geometric_summary.issue_types).length === 0 ? (
          <div className={styles.emptyBars}>No issues found</div>
        ) : (
          <div className={styles.barChart}>
            {Object.entries(geometric_summary.issue_types).map(
              ([type, count]) => (
                <div key={type} className={styles.barRow}>
                  <span className={styles.barLabel}>
                    {formatIssueType(type)}
                  </span>
                  <div className={styles.barTrack}>
                    <div
                      className={`${styles.barFill} ${
                        ISSUE_TYPE_COLORS[type] ?? styles.barFillPurple
                      }`}
                      style={{
                        width: `${(count / issueMax) * 100}%`,
                      }}
                    />
                  </div>
                  <span className={styles.barCount}>{count}</span>
                </div>
              )
            )}
          </div>
        )}

        <div
          className={styles.detailSectionTitle}
          style={{ marginTop: "1rem" }}
        >
          Suggested Labels
        </div>
        {Object.keys(geometric_summary.top_suggested_labels).length === 0 ? (
          <div className={styles.emptyBars}>None</div>
        ) : (
          <div className={styles.barChart}>
            {Object.entries(geometric_summary.top_suggested_labels).map(
              ([label, count]) => (
                <div key={label} className={styles.barRow}>
                  <span className={styles.barLabel}>{label}</span>
                  <div className={styles.barTrack}>
                    <div
                      className={`${styles.barFill} ${styles.barFillPurple}`}
                      style={{
                        width: `${(count / labelMax) * 100}%`,
                      }}
                    />
                  </div>
                  <span className={styles.barCount}>{count}</span>
                </div>
              )
            )}
          </div>
        )}
      </div>
    </div>
  );
}
