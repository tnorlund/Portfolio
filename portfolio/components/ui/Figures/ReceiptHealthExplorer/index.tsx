import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  ReceiptHealthCheck,
  ReceiptHealthLedgerIssue,
  ReceiptHealthReceipt,
  ReceiptHealthStatus,
  WithinReceiptWordDecision,
} from "../../../../types/api";
import {
  getBestImageUrl,
  getJpegFallbackUrl,
  usePreloadReceiptImages,
} from "../../../../utils/imageFormat";
import {
  DEFAULT_LAYOUT_VARS,
  ReceiptFlowLoadingShell,
} from "../ReceiptFlow/ReceiptFlowLoadingShell";
import { ReceiptFlowShell } from "../ReceiptFlow/ReceiptFlowShell";
import {
  getQueuePosition,
  getVisibleQueueIndices,
} from "../ReceiptFlow/receiptFlowUtils";
import type { ImageFormatSupport } from "../ReceiptFlow/types";
import { useImageFormatSupport } from "../ReceiptFlow/useImageFormatSupport";
import styles from "./ReceiptHealthExplorer.module.css";

type CheckId = ReceiptHealthCheck["id"];
type DetailId = CheckId | "issues" | "ledger" | "automation";

interface EvidenceWord {
  key: string;
  text: string;
  label: string | null;
  decision: string | null;
  bbox: { x: number; y: number; width: number; height: number };
}

const BATCH_SIZE = 12;
const INITIAL_SEED = 29;
const MAX_ISSUE_FETCHES = 3;
const FLOW_LAYOUT_VARS = {
  ...DEFAULT_LAYOUT_VARS,
  "--rf-align-items": "center",
} as React.CSSProperties;

const FLOW_MOCK_SCENARIOS: Array<{
  label: string;
  imageId: string;
  receiptId: number;
  focus: DetailId;
}> = [
  {
    label: "Clean",
    imageId: "9afeb902-28ff-436a-b69a-e0f5204eefa8",
    receiptId: 2,
    focus: "merchant_identity",
  },
  {
    label: "Merchant",
    imageId: "7d76a4bf-0deb-433b-9cc1-4561aa818061",
    receiptId: 1,
    focus: "merchant_identity",
  },
  {
    label: "Format",
    imageId: "ac5fd741-29f2-4e05-bf69-9c216ec8a56a",
    receiptId: 2,
    focus: "receipt_format",
  },
  {
    label: "Math",
    imageId: "946ba856-ae61-4428-bbc1-78ba268d6f0e",
    receiptId: 1,
    focus: "financial_math",
  },
  {
    label: "Known limit",
    imageId: "6539deb9-52cc-49a0-81a0-3a64989bee49",
    receiptId: 4,
    focus: "automation",
  },
  {
    label: "Consistent",
    imageId: "129ee4aa-2053-4cd7-8534-a9817f8a3402",
    receiptId: 2,
    focus: "automation",
  },
];

const CHECK_ORDER: CheckId[] = [
  "merchant_identity",
  "receipt_format",
  "financial_math",
];

const CHECK_LABELS: Record<CheckId, string> = {
  merchant_identity: "Merchant",
  receipt_format: "Format",
  financial_math: "Math",
};

const STATUS_LABELS: Record<ReceiptHealthStatus, string> = {
  pass: "Pass",
  review: "Review",
  fail: "Fail",
  not_applicable: "N/A",
};

const STATUS_CLASS: Record<ReceiptHealthStatus, string> = {
  pass: styles.statusPass,
  review: styles.statusReview,
  fail: styles.statusFail,
  not_applicable: styles.statusNeutral,
};

const STATUS_COLOR: Record<ReceiptHealthStatus, string> = {
  pass: "var(--color-green)",
  review: "var(--color-yellow)",
  fail: "var(--color-red)",
  not_applicable: "var(--text-color)",
};

const DECISION_COLOR: Record<string, string> = {
  VALID: "var(--color-green)",
  INVALID: "var(--color-red)",
  NEEDS_REVIEW: "var(--color-yellow)",
  CORRECTED: "var(--color-blue)",
};

const ISSUE_STATE_LABELS: Record<string, string> = {
  open: "Open",
  claimed: "Claimed",
  awaiting_validation: "Waiting",
  resolved: "Resolved",
  blocked: "Blocked",
  manual_review: "Review",
  known_limitation: "Known limit",
};

const PREFLIGHT_LABELS: Record<string, string> = {
  not_applicable: "N/A",
  needs_ai_review: "AI review",
  already_consistent: "Consistent",
  safe_exact_plan: "Safe plan",
  math_mismatch: "Math mismatch",
  known_limitation: "Known limit",
  reocr_needed: "Re-OCR",
  evaluator_rule_gap: "Rule gap",
};

function receiptKey(receipt: ReceiptHealthReceipt): string {
  return `${receipt.image_id}-${receipt.receipt_id}`;
}

function secondsLabel(seconds: number | null | undefined): string {
  if (seconds == null) return "No timing";
  if (seconds < 0.01) return "<10ms";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  return `${seconds.toFixed(1)}s`;
}

function statusClass(status: ReceiptHealthStatus): string {
  return `${styles.statusBadge} ${STATUS_CLASS[status]}`;
}

function issueLabel(count: number): string {
  if (count === 1) return "1 issue";
  return `${count} issues`;
}

function countLabel(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

function isCheckId(detailId: DetailId): detailId is CheckId {
  return CHECK_ORDER.includes(detailId as CheckId);
}

function scenarioForReceipt(
  receipt: ReceiptHealthReceipt | null,
): (typeof FLOW_MOCK_SCENARIOS)[number] | undefined {
  if (!receipt) return undefined;
  return FLOW_MOCK_SCENARIOS.find(
    (scenario) =>
      scenario.imageId === receipt.image_id &&
      scenario.receiptId === receipt.receipt_id,
  );
}

function normalizeDecisionWord(
  decision: WithinReceiptWordDecision,
  prefix: string,
): EvidenceWord | null {
  if (!decision.bbox) return null;
  return {
    key: `${prefix}-${decision.line_id}-${decision.word_id}`,
    text: decision.word_text,
    label: decision.current_label,
    decision: decision.decision,
    bbox: decision.bbox,
  };
}

function evidenceForCheck(
  receipt: ReceiptHealthReceipt,
  checkId: CheckId,
): EvidenceWord[] {
  if (checkId === "merchant_identity") {
    return receipt.place_validation.decisions
      .map((decision) => normalizeDecisionWord(decision, "place"))
      .filter((word): word is EvidenceWord => Boolean(word));
  }

  if (checkId === "receipt_format") {
    return receipt.format_validation.decisions
      .map((decision) => normalizeDecisionWord(decision, "format"))
      .filter((word): word is EvidenceWord => Boolean(word));
  }

  const byWord = new Map<string, EvidenceWord>();
  for (let equationIndex = 0; equationIndex < receipt.financial_math.equations.length; equationIndex += 1) {
    const equation = receipt.financial_math.equations[equationIndex];
    for (const word of equation.involved_words) {
      if (!word.bbox) continue;
      const key = `math-${word.line_id}-${word.word_id}`;
      const current = byWord.get(key);
      const next: EvidenceWord = {
        key: `${key}-${equationIndex}`,
        text: word.word_text,
        label: word.current_label,
        decision: word.decision,
        bbox: word.bbox,
      };
      if (!current || current.decision === "VALID") {
        byWord.set(key, next);
      }
    }
  }
  return Array.from(byWord.values());
}

function checkMethodLabel(check: ReceiptHealthCheck): string {
  if (check.id === "financial_math") {
    return "math rule";
  }

  return check.is_llm ? "LLM" : "rule";
}

function checkEvidenceLabel(check: ReceiptHealthCheck): string {
  return check.id === "financial_math"
    ? countLabel(check.evidence_count, "label box", "label boxes")
    : countLabel(check.evidence_count, "box", "boxes");
}

function mismatchCount(check: ReceiptHealthCheck): number {
  if (check.id !== "financial_math") return 0;
  return "mismatched_equations" in check.summary
    ? (check.summary.mismatched_equations ?? 0)
    : 0;
}

function checkDetailValue(check: ReceiptHealthCheck): string {
  const mismatches = mismatchCount(check);
  if (mismatches > 0) {
    return `${STATUS_LABELS[check.status]}: ${countLabel(mismatches, "mismatch")}`;
  }

  return STATUS_LABELS[check.status];
}

function checkDetailNote(check: ReceiptHealthCheck): string {
  const mismatches = mismatchCount(check);
  if (check.id === "financial_math" && mismatches > 0) {
    return "Green boxes mean the visible labels are valid. The check fails because the totals still do not reconcile.";
  }

  if (check.status === "fail" || check.status === "review") {
    return check.result;
  }

  return check.question;
}

function receiptTitle(receipt: ReceiptHealthReceipt): string {
  return receipt.merchant_name || `Receipt ${receipt.receipt_id}`;
}

function ReceiptImage({
  receipt,
  activeCheck,
  onImageUnavailable,
}: {
  receipt: ReceiptHealthReceipt;
  activeCheck: ReceiptHealthCheck;
  onImageUnavailable: (receipt: ReceiptHealthReceipt) => void;
}) {
  const formatSupport = useImageFormatSupport();
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const [imageFailed, setImageFailed] = useState(false);

  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(receipt, formatSupport, "medium");
  }, [formatSupport, receipt]);

  const evidence = useMemo(
    () => evidenceForCheck(receipt, activeCheck.id),
    [activeCheck.id, receipt],
  );

  const width = naturalSize?.width ?? receipt.width;
  const height = naturalSize?.height ?? receipt.height;
  const color = STATUS_COLOR[activeCheck.status];

  useEffect(() => {
    setImageFailed(false);
    setNaturalSize(null);
  }, [receipt.image_id, receipt.receipt_id]);

  if (!imageUrl || imageFailed) {
    return <div className={styles.imageLoading}>Loading receipt</div>;
  }

  return (
    <div className={styles.receiptImageStage}>
      <div className={styles.receiptImageFrame}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={imageUrl}
          alt={receiptTitle(receipt)}
          className={styles.receiptImage}
          onLoad={(event) => {
            const image = event.currentTarget;
            if (image.naturalWidth > 0 && image.naturalHeight > 0) {
              setNaturalSize({
                width: image.naturalWidth,
                height: image.naturalHeight,
              });
            }
          }}
          onError={(event) => {
            const fallback = getJpegFallbackUrl(receipt);
            if (event.currentTarget.src !== fallback) {
              event.currentTarget.src = fallback;
              return;
            }
            setImageFailed(true);
            onImageUnavailable(receipt);
          }}
        />
        <svg
          className={styles.overlay}
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          {evidence.map((word) => {
            const boxColor = DECISION_COLOR[word.decision ?? ""] ?? color;
            const x = word.bbox.x * width;
            const y = (1 - word.bbox.y - word.bbox.height) * height;
            const w = word.bbox.width * width;
            const h = word.bbox.height * height;

            return (
              <rect
                key={word.key}
                x={x}
                y={y}
                width={w}
                height={h}
                rx={2}
                fill={boxColor}
                fillOpacity={0.22}
                stroke={boxColor}
                strokeWidth={2}
                strokeOpacity={0.72}
              />
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function HealthSummary({ receipt }: { receipt: ReceiptHealthReceipt }) {
  return (
    <div className={styles.summaryGrid} aria-label="Receipt health summary">
      <div className={styles.summaryCell}>
        <span className={styles.summaryValue}>{receipt.summary.passed}</span>
        <span className={styles.summaryLabel}>Pass</span>
      </div>
      <div className={styles.summaryCell}>
        <span className={styles.summaryValue}>{receipt.summary.needs_review}</span>
        <span className={styles.summaryLabel}>Review</span>
      </div>
      <div className={styles.summaryCell}>
        <span className={styles.summaryValue}>{receipt.summary.failed}</span>
        <span className={styles.summaryLabel}>Fail</span>
      </div>
      <div className={styles.summaryCell}>
        <span className={styles.summaryValue}>{receipt.summary.not_applicable}</span>
        <span className={styles.summaryLabel}>N/A</span>
      </div>
    </div>
  );
}

function CheckButton({
  check,
  active,
  onSelect,
}: {
  check: ReceiptHealthCheck;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={`${styles.checkButton} ${active ? styles.checkButtonActive : ""}`}
      onClick={onSelect}
      aria-pressed={active}
    >
      <span className={styles.checkHeader}>
        <span className={styles.checkTitle}>{check.title}</span>
        <span className={statusClass(check.status)}>{STATUS_LABELS[check.status]}</span>
      </span>
      <span className={styles.checkResult}>{check.result}</span>
      <span className={styles.checkMeta}>
        <span>{check.evidence_count} evidence</span>
        <span>{secondsLabel(check.duration_seconds)}</span>
        <span>{check.is_llm ? "LLM" : "rule"}</span>
      </span>
    </button>
  );
}

function CheckDetails({ check }: { check: ReceiptHealthCheck }) {
  return (
    <div className={styles.checkDetails}>
      <div className={styles.checkQuestion}>{check.question}</div>
      <div className={styles.validatesList}>
        {check.what_it_validates.map((field) => (
          <span key={field} className={styles.fieldBadge}>
            {field}
          </span>
        ))}
      </div>
    </div>
  );
}

function Issues({ receipt }: { receipt: ReceiptHealthReceipt }) {
  if (receipt.primary_issues.length === 0) {
    return (
      <div className={styles.issueEmpty}>
        No primary issues for this receipt.
      </div>
    );
  }

  return (
    <div className={styles.issueList}>
      {receipt.primary_issues.map((issue) => (
        <div key={`${issue.check_id}-${issue.message}`} className={styles.issueItem}>
          <span className={statusClass(issue.status)}>{STATUS_LABELS[issue.status]}</span>
          <span className={styles.issueMessage}>{issue.message}</span>
        </div>
      ))}
    </div>
  );
}

function IssueLedger({
  issues,
  loading,
}: {
  issues: ReceiptHealthLedgerIssue[];
  loading: boolean;
}) {
  return (
    <div className={styles.ledgerList}>
      {loading ? (
        <div className={styles.issueEmpty}>Loading issue state.</div>
      ) : null}
      {!loading && issues.length === 0 ? (
        <div className={styles.issueEmpty}>No ledger state for this receipt.</div>
      ) : null}
      {issues.map((issue) => {
        const preflight = issue.preflight;
        const classification = preflight?.classification;
        return (
          <div key={issue.issue_id} className={styles.ledgerItem}>
            <div className={styles.ledgerItemHeader}>
              <span className={styles.ledgerIssueType}>{issue.issue_type}</span>
              <span className={styles.ledgerState}>
                {ISSUE_STATE_LABELS[issue.state ?? "open"] ?? issue.state ?? "Open"}
              </span>
            </div>
            <div className={styles.ledgerMessage}>{issue.message}</div>
            {preflight ? (
              <div className={styles.preflightRow}>
                <span className={styles.preflightBadge}>
                  {PREFLIGHT_LABELS[classification ?? ""] ?? classification}
                </span>
                <span className={styles.preflightSummary}>
                  {preflight.summary}
                </span>
              </div>
            ) : null}
            {preflight?.is_automation_ready ? (
              <div className={styles.actionCount}>
                {preflight.action_count} exact actions
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function ReceiptHealthFlowQueue({
  receipts,
  currentIndex,
  formatSupport,
  onSelect,
}: {
  receipts: ReceiptHealthReceipt[];
  currentIndex: number;
  formatSupport: ImageFormatSupport | null;
  onSelect: (index: number) => void;
}) {
  const visibleIndices = useMemo(() => {
    if (receipts.length === 0) return [];

    const nextIndices = getVisibleQueueIndices(receipts.length, currentIndex, 6, false);
    if (nextIndices.length > 0) return nextIndices;

    const start = Math.max(0, currentIndex - 6);
    return Array.from(
      { length: currentIndex - start },
      (_, offset) => start + offset,
    );
  }, [currentIndex, receipts.length]);

  if (!formatSupport || visibleIndices.length === 0) {
    return <div className={styles.flowReceiptQueue} />;
  }

  return (
    <div className={styles.flowReceiptQueue} data-rf-queue>
      {visibleIndices.map((receiptIndex, stackIndex) => {
        const receipt = receipts[receiptIndex];
        const imageUrl = getBestImageUrl(receipt, formatSupport, "thumbnail");
        const receiptId = `${receipt.image_id}_${receipt.receipt_id}`;
        const { rotation, leftOffset } = getQueuePosition(receiptId);

        return (
          <button
            key={`${receiptId}-mock-${receiptIndex}`}
            type="button"
            className={styles.flowQueuedReceipt}
            style={{
              top: `${stackIndex * 20}px`,
              left: `${10 + leftOffset}px`,
              transform: `rotate(${rotation}deg)`,
              zIndex: visibleIndices.length - stackIndex,
            }}
            onClick={() => onSelect(receiptIndex)}
            aria-label={`${receiptTitle(receipt)} status ${STATUS_LABELS[receipt.overall_status]}`}
          >
            {imageUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={imageUrl}
                alt=""
                className={styles.flowQueuedReceiptImage}
                onError={(event) => {
                  const fallback = getJpegFallbackUrl(receipt);
                  if (event.currentTarget.src !== fallback) {
                    event.currentTarget.src = fallback;
                  }
                }}
              />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

function ReceiptHealthFlowReceipt({
  receipt,
  activeCheck,
  onImageUnavailable,
}: {
  receipt: ReceiptHealthReceipt;
  activeCheck: ReceiptHealthCheck;
  onImageUnavailable: (receipt: ReceiptHealthReceipt) => void;
}) {
  const formatSupport = useImageFormatSupport();
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const [imageFailed, setImageFailed] = useState(false);

  const imageUrl = useMemo(() => {
    if (!formatSupport) return null;
    return getBestImageUrl(receipt, formatSupport, "medium");
  }, [formatSupport, receipt]);

  const evidence = useMemo(
    () => evidenceForCheck(receipt, activeCheck.id),
    [activeCheck.id, receipt],
  );

  useEffect(() => {
    setImageFailed(false);
    setNaturalSize(null);
  }, [receipt.image_id, receipt.receipt_id]);

  if (!imageUrl || imageFailed) {
    return <div className={styles.flowReceiptLoading}>Loading...</div>;
  }

  const width = naturalSize?.width ?? receipt.width;
  const height = naturalSize?.height ?? receipt.height;
  const color = STATUS_COLOR[activeCheck.status];

  return (
    <div className={styles.flowActiveReceipt}>
      <div className={styles.flowReceiptImageWrapper}>
        <div className={styles.flowReceiptImageInner}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUrl}
            alt={receiptTitle(receipt)}
            width={receipt.width}
            height={receipt.height}
            className={styles.flowReceiptImage}
            onLoad={(event) => {
              const image = event.currentTarget;
              if (image.naturalWidth > 0 && image.naturalHeight > 0) {
                setNaturalSize({
                  width: image.naturalWidth,
                  height: image.naturalHeight,
                });
              }
            }}
            onError={(event) => {
              const fallback = getJpegFallbackUrl(receipt);
              if (event.currentTarget.src !== fallback) {
                event.currentTarget.src = fallback;
                return;
              }
              setImageFailed(true);
              onImageUnavailable(receipt);
            }}
          />
          <svg
            className={styles.flowSvgOverlay}
            viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            {evidence.map((word) => {
              const boxColor = DECISION_COLOR[word.decision ?? ""] ?? color;
              const x = word.bbox.x * width;
              const y = (1 - word.bbox.y - word.bbox.height) * height;
              const w = word.bbox.width * width;
              const h = word.bbox.height * height;

              return (
                <rect
                  key={word.key}
                  x={x}
                  y={y}
                  width={w}
                  height={h}
                  rx={2}
                  fill={boxColor}
                  fillOpacity={0.22}
                  stroke={boxColor}
                  strokeWidth={2}
                  strokeOpacity={0.72}
                />
              );
            })}
          </svg>
        </div>
      </div>
    </div>
  );
}

function FlowLegendItem({
  label,
  value,
  color,
  active,
  onSelect,
}: {
  label: string;
  value: string;
  color: string;
  active?: boolean;
  onSelect?: () => void;
}) {
  const content = (
    <>
      <span className={styles.flowLegendDot} style={{ backgroundColor: color }} />
      <span className={styles.flowLegendLabel}>{label}</span>
      <span className={styles.flowLegendValue}>{value}</span>
    </>
  );

  if (!onSelect) {
    return (
      <div className={`${styles.flowLegendItem} ${active ? styles.flowLegendActive : ""}`}>
        {content}
      </div>
    );
  }

  return (
    <button
      type="button"
      className={`${styles.flowLegendItem} ${styles.flowLegendButton} ${active ? styles.flowLegendActive : ""}`}
      onClick={onSelect}
    >
      {content}
    </button>
  );
}

function automationColor(preflight: ReceiptHealthLedgerIssue["preflight"] | undefined): string {
  if (!preflight) return "rgba(var(--text-color-rgb, 0, 0, 0), 0.28)";
  if (preflight.is_automation_ready) return "var(--color-green)";

  switch (preflight.classification) {
    case "evaluator_rule_gap":
    case "math_mismatch":
      return "var(--color-red)";
    case "known_limitation":
    case "already_consistent":
      return "var(--color-blue)";
    case "reocr_needed":
      return "var(--color-red)";
    case "not_applicable":
      return "rgba(var(--text-color-rgb, 0, 0, 0), 0.28)";
    default:
      return "var(--color-yellow)";
  }
}

function automationDetailValue(
  preflight: ReceiptHealthLedgerIssue["preflight"] | undefined,
  loading: boolean,
): string {
  if (loading) return "Loading";
  if (!preflight) return "No action";
  if (preflight.is_automation_ready) return "Agent ready";

  switch (preflight.classification) {
    case "known_limitation":
    case "already_consistent":
      return "No label edit";
    case "evaluator_rule_gap":
    case "math_mismatch":
      return "Rule gap";
    case "reocr_needed":
      return "Re-OCR";
    case "needs_ai_review":
      return "Needs review";
    case "not_applicable":
      return "N/A";
    default:
      return "Agent hold";
  }
}

function automationDetailNote(
  preflight: ReceiptHealthLedgerIssue["preflight"] | undefined,
): string {
  if (!preflight) {
    return "No deterministic cleanup action is queued for this receipt.";
  }

  if (preflight.is_automation_ready) {
    return `${countLabel(preflight.action_count, "exact action")} stored for the cleanup agent.`;
  }

  switch (preflight.classification) {
    case "known_limitation":
    case "already_consistent":
      return `${preflight.summary} The agent has enough context to avoid retrying this edit, not to change labels.`;
    case "evaluator_rule_gap":
    case "math_mismatch":
      return `${preflight.summary} Keep this out of automation until the inputs or evaluator change.`;
    case "reocr_needed":
      return `${preflight.summary} Send this through OCR/parser repair before label cleanup.`;
    case "needs_ai_review":
      return `${preflight.summary} No exact deterministic action plan is stored.`;
    case "not_applicable":
      return preflight.summary;
    default:
      return preflight.summary;
  }
}

function ReceiptHealthFlowLegend({
  receipt,
  checks,
  activeCheck,
  activeDetailId,
  ledgerIssues,
  loadingLedgerIssues,
  onSelectCheck,
  onSelectDetail,
}: {
  receipt: ReceiptHealthReceipt;
  checks: ReceiptHealthCheck[];
  activeCheck: ReceiptHealthCheck;
  activeDetailId: DetailId;
  ledgerIssues: ReceiptHealthLedgerIssue[];
  loadingLedgerIssues: boolean;
  onSelectCheck: (checkId: CheckId) => void;
  onSelectDetail: (detailId: DetailId) => void;
}) {
  const firstPreflight = ledgerIssues.find((issue) => issue.preflight)?.preflight;
  const firstLedgerIssue = ledgerIssues[0] ?? null;
  const firstPrimaryIssue = receipt.primary_issues[0] ?? null;
  const automationLabel = firstPreflight
    ? (PREFLIGHT_LABELS[firstPreflight.classification] ?? firstPreflight.classification)
    : loadingLedgerIssues
      ? "Loading"
      : "No action";
  const automationStatusColor = automationColor(firstPreflight);

  return (
    <aside className={styles.flowEntityLegend}>
      <div className={styles.flowLegendDesktop}>
        {checks.map((check) => (
          <FlowLegendItem
            key={check.id}
            label={CHECK_LABELS[check.id]}
            value={STATUS_LABELS[check.status]}
            color={STATUS_COLOR[check.status]}
            active={activeDetailId === check.id}
            onSelect={() => {
              onSelectCheck(check.id);
              onSelectDetail(check.id);
            }}
          />
        ))}
        <FlowLegendItem
          label="Issues"
          value={receipt.summary.issue_count.toString()}
          color={receipt.summary.issue_count > 0 ? "var(--color-red)" : "var(--color-green)"}
          active={activeDetailId === "issues"}
          onSelect={() => onSelectDetail("issues")}
        />
        <FlowLegendItem
          label="Ledger"
          value={loadingLedgerIssues ? "..." : ledgerIssues.length.toString()}
          color={ledgerIssues.length > 0 ? "var(--color-yellow)" : "rgba(var(--text-color-rgb, 0, 0, 0), 0.28)"}
          active={activeDetailId === "ledger"}
          onSelect={() => onSelectDetail("ledger")}
        />
        <FlowLegendItem
          label="Automation"
          value={automationLabel}
          color={automationStatusColor}
          active={activeDetailId === "automation"}
          onSelect={() => onSelectDetail("automation")}
        />
      </div>

      <div className={styles.flowLegendMobile}>
        {checks.map((check) => (
          <FlowLegendItem
            key={check.id}
            label={CHECK_LABELS[check.id]}
            value={STATUS_LABELS[check.status]}
            color={STATUS_COLOR[check.status]}
            active={activeDetailId === check.id}
            onSelect={() => {
              onSelectCheck(check.id);
              onSelectDetail(check.id);
            }}
          />
        ))}
      </div>

      <div className={styles.flowInferenceTime}>
        <span className={styles.flowInferenceLabel}>
          {scenarioForReceipt(receipt)?.label ?? receiptTitle(receipt)}
        </span>
        {isCheckId(activeDetailId) ? (
          <>
            <span className={styles.flowInferenceValue}>
              {checkDetailValue(activeCheck)}
            </span>
            <span className={styles.flowInferenceMeta}>
              {checkEvidenceLabel(activeCheck)} · {secondsLabel(activeCheck.duration_seconds)} · {checkMethodLabel(activeCheck)}
            </span>
            <span className={styles.flowInferenceNote}>
              {checkDetailNote(activeCheck)}
            </span>
          </>
        ) : null}
        {activeDetailId === "issues" ? (
          <>
            <span className={styles.flowInferenceValue}>
              {issueLabel(receipt.summary.issue_count)}
            </span>
            <span className={styles.flowInferenceMeta}>
              {firstPrimaryIssue?.message ?? "No primary issues on this receipt."}
            </span>
          </>
        ) : null}
        {activeDetailId === "ledger" ? (
          <>
            <span className={styles.flowInferenceValue}>
              {loadingLedgerIssues
                ? "Loading"
                : firstLedgerIssue
                  ? (ISSUE_STATE_LABELS[firstLedgerIssue.state ?? "open"] ?? firstLedgerIssue.state ?? "Open")
                  : "Empty"}
            </span>
            <span className={styles.flowInferenceMeta}>
              {firstLedgerIssue?.message ?? "No durable issue state for this receipt."}
            </span>
          </>
        ) : null}
        {activeDetailId === "automation" ? (
          <>
            <span className={styles.flowInferenceValue}>
              {automationDetailValue(firstPreflight, loadingLedgerIssues)}
            </span>
            <span className={styles.flowInferenceMeta}>
              {firstPreflight
                ? (PREFLIGHT_LABELS[firstPreflight.classification] ?? firstPreflight.classification)
                : automationLabel}
            </span>
            <span className={styles.flowInferenceNote}>
              {automationDetailNote(firstPreflight)}
            </span>
          </>
        ) : null}
      </div>
    </aside>
  );
}

function ReceiptStackNav({
  receipts,
  currentIndex,
  totalCount,
  formatSupport,
  canGoPrevious,
  canGoNext,
  loadingMore,
  onPrevious,
  onNext,
  onSelect,
}: {
  receipts: ReceiptHealthReceipt[];
  currentIndex: number;
  totalCount: number;
  formatSupport: ImageFormatSupport | null;
  canGoPrevious: boolean;
  canGoNext: boolean;
  loadingMore: boolean;
  onPrevious: () => void;
  onNext: () => void;
  onSelect: (index: number) => void;
}) {
  const maxVisible = 7;
  const visibleIndices = useMemo(() => {
    if (receipts.length === 0) return [];

    const nextIndices = getVisibleQueueIndices(
      receipts.length,
      currentIndex,
      maxVisible - 1,
      false,
    );

    if (nextIndices.length >= maxVisible - 1 || currentIndex === 0) {
      return [currentIndex, ...nextIndices];
    }

    const previousSlots = maxVisible - 1 - nextIndices.length;
    const previousStart = Math.max(0, currentIndex - previousSlots);
    const previousIndices = Array.from(
      { length: currentIndex - previousStart },
      (_, offset) => previousStart + offset,
    );

    return [...previousIndices, currentIndex, ...nextIndices];
  }, [currentIndex, receipts.length]);

  return (
    <aside className={styles.stackPane} aria-label="Receipt stack">
      <div className={styles.stackHeader}>
        <span>Stack</span>
        <span>{currentIndex + 1}/{totalCount || receipts.length}</span>
      </div>

      <div className={styles.receiptStackViewport}>
        {visibleIndices.map((receiptIndex, stackIndex) => {
          const receipt = receipts[receiptIndex];
          const receiptId = `${receipt.image_id}_${receipt.receipt_id}`;
          const imageUrl = formatSupport
            ? getBestImageUrl(receipt, formatSupport, "thumbnail")
            : null;
          const { rotation, leftOffset } = getQueuePosition(receiptId);
          const isActive = receiptIndex === currentIndex;
          const stackOffset = stackIndex * 42;

          return (
            <button
              key={`${receiptId}-${receiptIndex}`}
              type="button"
              className={`${styles.stackReceiptButton} ${isActive ? styles.stackReceiptActive : ""}`}
              style={{
                top: `${stackOffset}px`,
                left: `${28 + leftOffset}px`,
                transform: `rotate(${rotation}deg)`,
                zIndex: visibleIndices.length - stackIndex,
              }}
              onClick={() => onSelect(receiptIndex)}
              aria-label={`${receiptTitle(receipt)} status ${STATUS_LABELS[receipt.overall_status]}`}
              aria-current={isActive ? "true" : undefined}
            >
              <span className={`${styles.stackStatusPip} ${STATUS_CLASS[receipt.overall_status]}`} />
              {imageUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={imageUrl}
                  alt=""
                  className={styles.stackReceiptImage}
                  onError={(event) => {
                    const fallback = getJpegFallbackUrl(receipt);
                    if (event.currentTarget.src !== fallback) {
                      event.currentTarget.src = fallback;
                    }
                  }}
                />
              ) : (
                <span className={styles.stackReceiptPlaceholder}>
                  {receipt.receipt_id}
                </span>
              )}
              {receipt.summary.issue_count > 0 ? (
                <span className={styles.stackIssuePill}>
                  {receipt.summary.issue_count}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>

      <div className={styles.stackControls}>
        <button
          type="button"
          className={styles.navButton}
          onClick={onPrevious}
          disabled={!canGoPrevious}
        >
          Prev
        </button>
        <button
          type="button"
          className={styles.navButton}
          onClick={onNext}
          disabled={!canGoNext || loadingMore}
        >
          {loadingMore ? "Loading" : "Next"}
        </button>
      </div>
    </aside>
  );
}

export default function ReceiptHealthExplorer() {
  const { ref, inView: nearViewport } = useInView({
    triggerOnce: true,
    rootMargin: "400px",
  });

  const [receipts, setReceipts] = useState<ReceiptHealthReceipt[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [activeCheckId, setActiveCheckId] = useState<CheckId>("merchant_identity");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [findingIssue, setFindingIssue] = useState(false);
  const [ledgerIssues, setLedgerIssues] = useState<ReceiptHealthLedgerIssue[]>([]);
  const [loadingLedgerIssues, setLoadingLedgerIssues] = useState(false);
  const [activeDetailId, setActiveDetailId] = useState<DetailId>("merchant_identity");
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [totalCount, setTotalCount] = useState(0);
  const [unavailableImageKeys, setUnavailableImageKeys] = useState<Set<string>>(
    () => new Set(),
  );
  const fetchedInitial = useRef(false);
  const formatSupport = useImageFormatSupport();

  usePreloadReceiptImages(
    receipts.slice(currentIndex, currentIndex + 2),
    formatSupport,
  );

  const loadReceipts = useCallback(
    async (offset: number) => {
      const response = await api.fetchReceiptHealth(BATCH_SIZE, INITIAL_SEED, offset);
      setTotalCount(response.total_count);
      setHasMore(response.has_more);
      setReceipts((current) => {
        if (offset === 0) return response.receipts;
        const existing = new Set(
          current.map((receipt) => `${receipt.image_id}-${receipt.receipt_id}`),
        );
        const next = response.receipts.filter(
          (receipt) => !existing.has(`${receipt.image_id}-${receipt.receipt_id}`),
        );
        return [...current, ...next];
      });
      return response.receipts;
    },
    [],
  );

  const loadScenarioReceipts = useCallback(async () => {
    const scenarioResults = await Promise.all(
      FLOW_MOCK_SCENARIOS.map(async (scenario) => {
        const response = await api.fetchReceiptHealth(
          BATCH_SIZE,
          INITIAL_SEED,
          0,
          { imageId: scenario.imageId },
        );
        return response.receipts.find(
          (receipt) => receipt.receipt_id === scenario.receiptId,
        ) ?? null;
      }),
    );

    const scenarioReceipts = scenarioResults.filter(
      (receipt): receipt is ReceiptHealthReceipt => Boolean(receipt),
    );

    setTotalCount(scenarioReceipts.length);
    setHasMore(false);
    setReceipts(scenarioReceipts);
    return scenarioReceipts;
  }, []);

  useEffect(() => {
    if (!nearViewport || fetchedInitial.current) return;
    fetchedInitial.current = true;

    let cancelled = false;
    setLoading(true);
    loadScenarioReceipts()
      .catch((err) => {
        if (!cancelled) {
          console.error("Failed to fetch receipt health data:", err);
          setError(err instanceof Error ? err.message : "Failed to load receipt health data");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [loadScenarioReceipts, nearViewport]);

  const currentReceipt = receipts[currentIndex] ?? null;

  useEffect(() => {
    if (!currentReceipt) {
      setLedgerIssues([]);
      return;
    }

    let cancelled = false;
    setLoadingLedgerIssues(true);
    setLedgerIssues([]);
    api.fetchReceiptHealthIssues({
      state: "all",
      imageId: currentReceipt.image_id,
      receiptId: currentReceipt.receipt_id,
      limit: 50,
    })
      .then((response) => {
        if (!cancelled) {
          setLedgerIssues(response.issues);
        }
      })
      .catch((err) => {
        console.error("Failed to fetch receipt health issues:", err);
        if (!cancelled) {
          setLedgerIssues([]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingLedgerIssues(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [currentReceipt]);

  const orderedChecks = useMemo(() => {
    if (!currentReceipt) return [];
    return CHECK_ORDER
      .map((id) => currentReceipt.checks.find((check) => check.id === id))
      .filter((check): check is ReceiptHealthCheck => Boolean(check));
  }, [currentReceipt]);

  const activeCheck = useMemo(() => {
    return (
      orderedChecks.find((check) => check.id === activeCheckId) ??
      orderedChecks[0] ??
      null
    );
  }, [activeCheckId, orderedChecks]);

  useEffect(() => {
    if (!activeCheck && orderedChecks[0]) {
      setActiveCheckId(orderedChecks[0].id);
    }
  }, [activeCheck, orderedChecks]);

  const selectReceipt = useCallback((index: number) => {
    setCurrentIndex(index);
    const receipt = receipts[index];
    const scenario = scenarioForReceipt(receipt ?? null);
    if (scenario) {
      setActiveDetailId(scenario.focus);
      if (isCheckId(scenario.focus)) {
        setActiveCheckId(scenario.focus);
      } else if (receipt?.checks.some((check) => check.id === "financial_math")) {
        setActiveCheckId("financial_math");
      }
      return;
    }

    const nextCheck = CHECK_ORDER.find((id) =>
      receipt?.checks.some((check) => check.id === id),
    );
    if (nextCheck) {
      setActiveCheckId(nextCheck);
      setActiveDetailId(nextCheck);
    }
  }, [receipts]);

  const handleImageUnavailable = useCallback((receipt: ReceiptHealthReceipt) => {
    setUnavailableImageKeys((current) => {
      const key = receiptKey(receipt);
      if (current.has(key)) return current;
      const next = new Set(current);
      next.add(key);
      return next;
    });
  }, []);

  useEffect(() => {
    if (!currentReceipt) return;
    if (!unavailableImageKeys.has(receiptKey(currentReceipt))) return;

    const replacementIndex = receipts.findIndex(
      (receipt, index) =>
        index !== currentIndex && !unavailableImageKeys.has(receiptKey(receipt)),
    );
    if (replacementIndex >= 0) {
      selectReceipt(replacementIndex);
    }
  }, [
    currentIndex,
    currentReceipt,
    receipts,
    selectReceipt,
    unavailableImageKeys,
  ]);

  const goPrevious = useCallback(() => {
    setCurrentIndex((index) => Math.max(0, index - 1));
  }, []);

  const goNext = useCallback(async () => {
    if (currentIndex < receipts.length - 1) {
      selectReceipt(currentIndex + 1);
      return;
    }

    if (!hasMore || loadingMore) return;
    setLoadingMore(true);
    try {
      const previousLength = receipts.length;
      const loaded = await loadReceipts(previousLength);
      if (loaded.length > 0) {
        setCurrentIndex(previousLength);
      }
    } catch (err) {
      console.error("Failed to fetch more receipt health data:", err);
      setError(err instanceof Error ? err.message : "Failed to load more receipts");
    } finally {
      setLoadingMore(false);
    }
  }, [currentIndex, hasMore, loadReceipts, loadingMore, receipts.length, selectReceipt]);

  const selectFirstIssueCheck = useCallback((receipt: ReceiptHealthReceipt) => {
    const issueCheckId = receipt.primary_issues[0]?.check_id;
    if (issueCheckId && receipt.checks.some((check) => check.id === issueCheckId)) {
      setActiveCheckId(issueCheckId);
      return;
    }

    const firstNonPassingCheck = receipt.checks.find((check) =>
      check.status === "fail" || check.status === "review"
    );
    if (firstNonPassingCheck) {
      setActiveCheckId(firstNonPassingCheck.id);
    }
  }, []);

  const goNextIssue = useCallback(async () => {
    if (findingIssue || loadingMore) return;
    setFindingIssue(true);
    try {
      let searchStart = currentIndex + 1;
      let loadedReceipts = receipts;

      for (let fetchCount = 0; fetchCount <= MAX_ISSUE_FETCHES; fetchCount += 1) {
        const issueIndex = loadedReceipts.findIndex(
          (receipt, index) =>
            index >= searchStart &&
            receipt.summary.issue_count > 0 &&
            !unavailableImageKeys.has(receiptKey(receipt)),
        );

        if (issueIndex >= 0) {
          setCurrentIndex(issueIndex);
          selectFirstIssueCheck(loadedReceipts[issueIndex]);
          return;
        }

        if (!hasMore) return;

        const previousLength = loadedReceipts.length;
        const loaded = await loadReceipts(previousLength);
        if (loaded.length === 0) return;

        const existing = new Set(
          loadedReceipts.map((receipt) => receiptKey(receipt)),
        );
        loadedReceipts = [
          ...loadedReceipts,
          ...loaded.filter((receipt) => !existing.has(receiptKey(receipt))),
        ];
        searchStart = previousLength;
      }
    } catch (err) {
      console.error("Failed to find issue receipt:", err);
      setError(err instanceof Error ? err.message : "Failed to find issue receipt");
    } finally {
      setFindingIssue(false);
    }
  }, [
    currentIndex,
    findingIssue,
    hasMore,
    loadReceipts,
    loadingMore,
    receipts,
    selectFirstIssueCheck,
    unavailableImageKeys,
  ]);

  if (loading) {
    return (
      <div ref={ref} className={styles.container} data-testid="receipt-health-explorer">
        <ReceiptFlowLoadingShell
          variant="within"
          layoutVars={{
            ...DEFAULT_LAYOUT_VARS,
            "--rf-center-height": "560px",
          } as React.CSSProperties}
        />
      </div>
    );
  }

  if (error || !currentReceipt || !activeCheck) {
    return (
      <div ref={ref} className={styles.container} data-testid="receipt-health-explorer">
        <ReceiptFlowLoadingShell
          variant="within"
          message={error ?? "No receipt health data available"}
          isError={Boolean(error)}
        />
      </div>
    );
  }

  const canGoPrevious = currentIndex > 0;
  const canGoNext = currentIndex < receipts.length - 1 || hasMore;
  const hasLoadedIssueAhead = receipts.some(
    (receipt, index) =>
      index > currentIndex &&
      receipt.summary.issue_count > 0 &&
      !unavailableImageKeys.has(receiptKey(receipt)),
  );
  const canGoNextIssue = hasLoadedIssueAhead || hasMore;

  return (
    <div
      ref={ref}
      className={`${styles.container} ${styles.flowMockContainer}`}
      data-testid="receipt-health-explorer"
    >
      <ReceiptFlowShell
        layoutVars={FLOW_LAYOUT_VARS}
        isTransitioning={false}
        queue={
          <ReceiptHealthFlowQueue
            receipts={receipts}
            currentIndex={currentIndex}
            formatSupport={formatSupport}
            onSelect={selectReceipt}
          />
        }
        center={
          <ReceiptHealthFlowReceipt
            receipt={currentReceipt}
            activeCheck={activeCheck}
            onImageUnavailable={handleImageUnavailable}
          />
        }
        legend={
          <ReceiptHealthFlowLegend
            receipt={currentReceipt}
            checks={orderedChecks}
            activeCheck={activeCheck}
            activeDetailId={activeDetailId}
            ledgerIssues={ledgerIssues}
            loadingLedgerIssues={loadingLedgerIssues}
            onSelectCheck={setActiveCheckId}
            onSelectDetail={setActiveDetailId}
          />
        }
      />
    </div>
  );
}
