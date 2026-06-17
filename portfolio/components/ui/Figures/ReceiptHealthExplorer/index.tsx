import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  ReceiptHealthCheck,
  ReceiptHealthLedgerIssue,
  ReceiptHealthLedgerSummary,
  ReceiptHealthLineItemAmountEvidence,
  ReceiptHealthReceipt,
  ReceiptHealthSectionEvidence,
  ReceiptHealthStatus,
  WithinReceiptWordDecision,
} from "../../../../types/api";
import {
  getBestImageUrl,
  getJpegFallbackUrl,
  usePreloadReceiptImages,
} from "../../../../utils/imageFormat";
import { ReceiptFlowLoadingShell } from "../ReceiptFlow/ReceiptFlowLoadingShell";
import { FlyingReceipt } from "../ReceiptFlow/FlyingReceipt";
import { ReceiptFlowShell } from "../ReceiptFlow/ReceiptFlowShell";
import {
  getQueuePosition,
  getVisibleQueueIndices,
} from "../ReceiptFlow/receiptFlowUtils";
import type { ImageFormatSupport } from "../ReceiptFlow/types";
import { useFlyingReceipt } from "../ReceiptFlow/useFlyingReceipt";
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
const AUTO_ROTATE_MS = 5200;
const MANUAL_ROTATE_PAUSE_MS = 8000;
const TRANSITION_DURATION_MS = 600;
const FLYING_RECEIPT_MAX_WIDTH = 350;
const FLYING_RECEIPT_MAX_HEIGHT = 500;
const FLOW_LAYOUT_VARS = {
  "--rf-queue-width": "280px",
  "--rf-queue-height": "400px",
  "--rf-center-max-width": "350px",
  "--rf-center-height": "500px",
  "--rf-legend-width": "280px",
  "--rf-legend-height": "500px",
  "--rf-legend-stage-height": "280px",
  "--rf-mobile-center-height": "400px",
  "--rf-mobile-center-height-sm": "320px",
  "--rf-mobile-legend-height": "212px",
  "--rf-mobile-shell-height": "628px",
  "--rf-mobile-shell-height-sm": "548px",
  "--rf-gap": "1.5rem",
  "--rf-align-items": "center",
} as React.CSSProperties;

type LedgerContext = {
  issues: ReceiptHealthLedgerIssue[];
  summary: ReceiptHealthLedgerSummary | null;
};

const emptyLedgerContext: LedgerContext = {
  issues: [],
  summary: null,
};

function imageUrlMatches(src: string, target: string): boolean {
  if (!src) return false;

  try {
    return src === new URL(target, window.location.href).href;
  } catch {
    return src === target;
  }
}

function setReceiptImageFallback(image: HTMLImageElement, fallbackUrl: string): boolean {
  if (imageUrlMatches(image.currentSrc || image.src, fallbackUrl)) {
    return false;
  }

  image.src = fallbackUrl;
  return true;
}

function preloadReceiptImageForTransition(
  receipt: ReceiptHealthReceipt,
  formatSupport: ImageFormatSupport,
): Promise<void> {
  const imageUrl = getBestImageUrl(receipt, formatSupport, "medium");
  const fallbackUrl = getJpegFallbackUrl(receipt);

  return new Promise((resolve) => {
    let settled = false;
    let attemptedFallback = imageUrl === fallbackUrl;
    const image = new Image();
    const finish = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    const timeout = window.setTimeout(finish, 2500);
    const finishAndClear = () => {
      window.clearTimeout(timeout);
      finish();
    };

    image.onload = () => {
      const decode = image.decode?.();
      if (decode) {
        decode.then(finishAndClear).catch(finishAndClear);
        return;
      }
      finishAndClear();
    };
    image.onerror = () => {
      if (!attemptedFallback) {
        attemptedFallback = true;
        image.src = fallbackUrl;
        return;
      }
      finishAndClear();
    };
    image.src = imageUrl;
  });
}

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
    label: "OCR gap",
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

function orderedChecksForReceipt(
  receipt: ReceiptHealthReceipt | null | undefined,
): ReceiptHealthCheck[] {
  if (!receipt) return [];
  return CHECK_ORDER
    .map((id) => receipt.checks.find((check) => check.id === id))
    .filter((check): check is ReceiptHealthCheck => Boolean(check));
}

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

const ROOT_CAUSE_LABELS: Record<string, string> = {
  already_consistent_labels: "Consistent",
  business_name_token_mislabeled_store_hours: "Merchant token",
  line_item_price_tokenization_gap: "Item prices split by OCR",
  line_total_candidates_do_not_reconcile: "Line totals",
  malformed_amount_text: "Amount OCR",
  missing_line_totals: "Line totals",
  missing_line_totals_with_tax_discount: "Tax path",
  missing_line_totals_with_tip_gratuity: "Tip path",
  tip_gratuity_ambiguity: "Tip region",
  void_discount_formula_rule: "Void/discount",
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

function preferredCheckIdForReceipt(
  receipt: ReceiptHealthReceipt | null | undefined,
): CheckId | null {
  const scenario = scenarioForReceipt(receipt ?? null);
  if (scenario) {
    if (isCheckId(scenario.focus)) return scenario.focus;
    if (receipt?.checks.some((check) => check.id === "financial_math")) {
      return "financial_math";
    }
  }

  return (
    CHECK_ORDER.find((id) =>
      receipt?.checks.some((check) => check.id === id),
    ) ?? null
  );
}

function receiptDisplaySize(receipt: ReceiptHealthReceipt): {
  displayWidth: number;
  displayHeight: number;
} {
  const sourceWidth = Math.max(receipt.width || FLYING_RECEIPT_MAX_WIDTH, 1);
  const sourceHeight = Math.max(receipt.height || FLYING_RECEIPT_MAX_HEIGHT, 1);
  const aspectRatio = sourceWidth / sourceHeight;
  let displayHeight = Math.min(FLYING_RECEIPT_MAX_HEIGHT, sourceHeight);
  let displayWidth = displayHeight * aspectRatio;

  if (displayWidth > FLYING_RECEIPT_MAX_WIDTH) {
    displayWidth = FLYING_RECEIPT_MAX_WIDTH;
    displayHeight = displayWidth / aspectRatio;
  }

  return { displayWidth, displayHeight };
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
            if (setReceiptImageFallback(event.currentTarget, fallback)) {
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

function sectionLabel(value: string): string {
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function rootCauseLabel(value: string | undefined): string {
  if (!value) return "No issue";
  return ROOT_CAUSE_LABELS[value] ?? sectionLabel(value);
}

function compactText(value: string | null | undefined, fallback: string): string {
  const text = value?.trim();
  return text && text.length > 0 ? text : fallback;
}

function sectionEntries(
  sections: Record<string, number> | undefined,
): Array<[string, number]> {
  if (!sections) return [];
  return Object.entries(sections)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);
}

function SectionEvidence({
  evidence,
}: {
  evidence: ReceiptHealthSectionEvidence | undefined;
}) {
  const issueSections = sectionEntries(evidence?.issue_sections);
  const contextSections = sectionEntries(evidence?.context_sections);
  const rows = evidence?.issue_rows ?? [];

  if (!evidence || (issueSections.length === 0 && rows.length === 0)) {
    return null;
  }

  return (
    <div className={styles.sectionEvidence}>
      {issueSections.length > 0 ? (
        <div className={styles.sectionChips}>
          {issueSections.map(([section, count]) => (
            <span key={section} className={styles.sectionChip}>
              {sectionLabel(section)}
              {count > 1 ? ` ${count}` : ""}
            </span>
          ))}
        </div>
      ) : null}
      {rows.length > 0 ? (
        <div className={styles.sectionRows}>
          {rows.slice(0, 3).map((row) => (
            <div
              key={`${row.row_index}-${row.line_ids.join("-")}`}
              className={styles.sectionRow}
            >
              <span className={styles.sectionRowLabel}>
                {sectionLabel(row.section)}
              </span>
              <span className={styles.sectionRowText}>{row.text}</span>
            </div>
          ))}
        </div>
      ) : null}
      {contextSections.length > 0 ? (
        <div className={styles.sectionContext}>
          Nearby:{" "}
          {contextSections
            .slice(0, 3)
            .map(([section]) => sectionLabel(section))
            .join(", ")}
        </div>
      ) : null}
    </div>
  );
}

function sortedCountEntries(
  counts: Record<string, number> | undefined,
): Array<[string, number]> {
  if (!counts) return [];
  return Object.entries(counts)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);
}

function hasClassifiedPreflight(issue: ReceiptHealthLedgerIssue): boolean {
  const classification = issue.preflight?.classification;
  return Boolean(classification && classification !== "unclassified");
}

function issueDisplayRank(issue: ReceiptHealthLedgerIssue): number {
  let rank = 0;
  if (issue.state !== "resolved") rank += 64;
  if (issue.preflight) rank += 32;
  if (hasClassifiedPreflight(issue)) rank += 16;
  if (issue.preflight?.evidence?.section_evidence) rank += 8;
  if (issue.preflight?.root_cause) rank += 4;
  if (issue.preflight?.is_automation_ready) rank += 2;
  return rank;
}

function selectDisplayIssue(
  issues: ReceiptHealthLedgerIssue[],
  checkId?: CheckId,
): ReceiptHealthLedgerIssue | null {
  const candidates = checkId
    ? issues.filter((issue) => issue.check_id === checkId)
    : issues;
  if (candidates.length === 0) return null;

  return [...candidates].sort(
    (a, b) =>
      issueDisplayRank(b) - issueDisplayRank(a) ||
      (b.last_seen_at ?? b.observed_at ?? "").localeCompare(a.last_seen_at ?? a.observed_at ?? ""),
  )[0] ?? null;
}

function validationOutcomeLabel(status: ReceiptHealthStatus): string {
  switch (status) {
    case "pass":
      return "VALID";
    case "fail":
      return "INVALID";
    case "review":
      return "REVIEW";
    case "not_applicable":
      return "N/A";
    default:
      return "";
  }
}

function statusToneClass(status: ReceiptHealthStatus): string {
  switch (status) {
    case "pass":
      return styles.railStatusPass;
    case "fail":
      return styles.railStatusFail;
    case "review":
      return styles.railStatusReview;
    case "not_applicable":
      return styles.railStatusNeutral;
    default:
      return "";
  }
}

function ValidationStatusIcon({ status }: { status: ReceiptHealthStatus }) {
  return (
    <svg
      className={`${styles.validationStatusIcon} ${statusToneClass(status)}`}
      viewBox="0 0 14 14"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="7" cy="7" r="6" />
      {status === "pass" ? (
        <path
          d="M4 7 L6 9.4 L10 5"
          fill="none"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.8"
        />
      ) : null}
      {status === "fail" ? (
        <g>
          <line
            x1="4.5"
            y1="4.5"
            x2="9.5"
            y2="9.5"
            stroke="currentColor"
            strokeLinecap="round"
            strokeWidth="1.8"
          />
          <line
            x1="9.5"
            y1="4.5"
            x2="4.5"
            y2="9.5"
            stroke="currentColor"
            strokeLinecap="round"
            strokeWidth="1.8"
          />
        </g>
      ) : null}
      {status === "review" ? (
        <text x="7" y="10" textAnchor="middle">
          ?
        </text>
      ) : null}
      {status === "not_applicable" ? (
        <line
          x1="4.4"
          y1="7"
          x2="9.6"
          y2="7"
          stroke="currentColor"
          strokeLinecap="round"
          strokeWidth="1.8"
        />
      ) : null}
    </svg>
  );
}

function issueMessageForRail(
  receipt: ReceiptHealthReceipt,
  activeCheck: ReceiptHealthCheck,
  ledgerIssue: ReceiptHealthLedgerIssue | null,
): string {
  const primaryIssue = receipt.primary_issues.find(
    (issue) => issue.check_id === activeCheck.id,
  ) ?? receipt.primary_issues[0];

  return compactText(
    ledgerIssue?.message ?? primaryIssue?.message,
    activeCheck.result,
  );
}

function actionLabel(
  preflight: ReceiptHealthLedgerIssue["preflight"] | undefined,
  loading: boolean,
): string {
  if (loading) return "Loading";
  if (!preflight) return "No stored action";
  if (preflight.is_automation_ready) {
    return `${preflight.action_count} exact action${preflight.action_count === 1 ? "" : "s"}`;
  }
  switch (preflight.classification) {
    case "known_limitation":
    case "already_consistent":
      return "Do not retry";
    case "evaluator_rule_gap":
    case "math_mismatch":
      return "Hold writes";
    case "reocr_needed":
      return "Re-OCR";
    case "needs_ai_review":
      return "AI review";
    default:
      return "Hold";
  }
}

function routeLabel(
  preflight: ReceiptHealthLedgerIssue["preflight"] | undefined,
  loading: boolean,
): string {
  if (loading) return "Loading";
  if (!preflight) return "None";
  return PREFLIGHT_LABELS[preflight.classification] ?? preflight.classification;
}

function combinedRouteLabel(
  preflight: ReceiptHealthLedgerIssue["preflight"] | undefined,
  loading: boolean,
): string {
  const route = routeLabel(preflight, loading);
  if (!preflight?.lane) return route;

  const lane = sectionLabel(preflight.lane);
  const normalizedRoute = route.toLowerCase().replace(/[^a-z0-9]/g, "");
  const normalizedLane = lane.toLowerCase().replace(/[^a-z0-9]/g, "");
  if (normalizedRoute === normalizedLane) return route;
  if (normalizedRoute === "knownlimit" && normalizedLane === "knownlimitation") {
    return route;
  }
  return `${route} / ${lane}`;
}

function diagnosisCopy(
  receipt: ReceiptHealthReceipt,
  activeCheck: ReceiptHealthCheck,
  preflight: ReceiptHealthLedgerIssue["preflight"] | undefined,
  loading: boolean,
): { title: string; body: string; action: string } {
  if (receipt.summary.issue_count === 0) {
    return {
      title: "Labels validated",
      body: "Merchant, format, and math agree for this receipt.",
      action: "No cleanup needed",
    };
  }

  if (loading) {
    return {
      title: "Checking receipt",
      body: "Loading the stored validation issue.",
      action: "Loading",
    };
  }

  if (!preflight) {
    return {
      title: `${CHECK_LABELS[activeCheck.id]} failed`,
      body: "This receipt failed validation, but no cleanup route is stored yet.",
      action: "Hold label writes",
    };
  }

  switch (preflight.root_cause) {
    case "line_item_price_tokenization_gap":
      return {
        title: "Item prices split by OCR",
        body: "Visible item prices were fragmented, so the math check cannot validate line totals.",
        action: "OCR repair; no label write",
      };
    case "tip_gratuity_ambiguity":
    case "missing_line_totals_with_tip_gratuity":
      return {
        title: "Tip area is ambiguous",
        body: "The receipt shows tip-like numbers that should not be treated as paid totals without review.",
        action: "Hold label writes",
      };
    case "void_discount_formula_rule":
      return {
        title: "Void/discount rule gap",
        body: "The receipt has void or discount rows that need an evaluator rule before cleanup.",
        action: "Hold label writes",
      };
    case "already_consistent_labels":
      return {
        title: "Already consistent",
        body: "The current labels already satisfy this validation issue.",
        action: "Do not retry",
      };
    case "line_total_candidates_do_not_reconcile":
      return {
        title: "Line totals do not reconcile",
        body: "Candidate item totals exist, but they do not add up to the visible total path.",
        action: "Review rule; no label write",
      };
    default:
      if (preflight.is_automation_ready) {
        return {
          title: "Exact label fix found",
          body: "The stored actions can repair this receipt without inference.",
          action: `${preflight.action_count} exact label action${preflight.action_count === 1 ? "" : "s"}`,
        };
      }
      return {
        title: rootCauseLabel(preflight.root_cause),
        body: preflight.summary,
        action: actionLabel(preflight, false),
      };
  }
}

function coreOutcomeLabel(
  preflight: ReceiptHealthLedgerIssue["preflight"] | undefined,
  loading: boolean,
): string {
  if (loading) return "Checking";
  if (!preflight) return "No label write";
  if (preflight.is_automation_ready) return "Safe auto-fix";

  switch (preflight.classification) {
    case "reocr_needed":
      return "Needs re-OCR";
    case "known_limitation":
      return "Known limitation";
    case "needs_ai_review":
      return "Needs AI review";
    case "already_consistent":
      return "No label write";
    default:
      return "No label write";
  }
}

function rootCauseExplanation(
  rootCause: string | undefined,
  check: ReceiptHealthCheck,
  issue: ReceiptHealthLedgerIssue | null,
): string {
  switch (rootCause) {
    case "business_name_token_mislabeled_store_hours":
      return "Business name token labeled as store hours";
    case "generic_or_extra_merchant_name_token":
      return "Extra merchant-name token";
    case "numeric_token_mislabeled_loyalty_id":
      return "Number token labeled as loyalty ID";
    case "partial_phone_token":
      return "Partial phone token";
    case "partial_time_token":
      return "Partial time token";
    case "truncated_website_token":
      return "Truncated website token";
    case "line_item_price_tokenization_gap":
      return "Item prices split by OCR";
    case "missing_line_totals_with_tip_gratuity":
      return "Missing line totals near tip suggestions";
    case "missing_line_totals_with_tax_discount":
      return "Missing line totals near tax/discount rows";
    case "missing_line_totals":
      return "Missing line totals";
    case "tip_gratuity_ambiguity":
      return "Tip area is ambiguous";
    case "void_discount_formula_rule":
      return "Void/discount rule gap";
    case "line_total_candidates_do_not_reconcile":
      return "Line totals do not reconcile";
    case "already_consistent_labels":
      return "Already consistent";
    default:
      if (check.id === "merchant_identity") return "Merchant label needs review";
      if (check.id === "receipt_format") return "Format labels need review";
      if (check.id === "financial_math") return "Totals do not reconcile";
      return issue?.message ?? "Validation issue";
  }
}

function validationRowExplanation(
  check: ReceiptHealthCheck,
  issue: ReceiptHealthLedgerIssue | null,
  loading: boolean,
): string | null {
  if (check.status !== "fail" && check.status !== "review") return null;
  if (loading && !issue) return null;

  const preflight = issue?.preflight;
  const reason = rootCauseExplanation(preflight?.root_cause, check, issue);
  const outcome = coreOutcomeLabel(preflight, false);
  return `${reason} · ${outcome}`;
}

function ValidationReason({
  explanation,
  muted = false,
}: {
  explanation: string | null;
  muted?: boolean;
}) {
  const hasExplanation = Boolean(explanation);
  const visibleExplanation = muted ? null : explanation;
  return (
    <div
      className={[
        styles.validationReasonSlot,
        hasExplanation ? styles.validationReasonSlotVisible : "",
        muted ? styles.validationReasonSlotMuted : "",
      ].filter(Boolean).join(" ")}
      aria-live={muted ? "off" : "polite"}
      aria-hidden={muted || !hasExplanation}
    >
      <div className={styles.validationReasonClip}>
        <div
          key={visibleExplanation ?? (muted ? "muted" : "empty")}
          className={[
            styles.validationReasonInner,
            visibleExplanation ? styles.validationReasonInnerVisible : "",
          ].filter(Boolean).join(" ")}
        >
          {visibleExplanation}
        </div>
      </div>
    </div>
  );
}

function classifiedRootCauseTotal(summary: ReceiptHealthLedgerSummary | null): number {
  return sortedCountEntries(summary?.by_preflight_root_cause)
    .filter(([rootCause]) => rootCause !== "unclassified")
    .reduce((sum, [, count]) => sum + count, 0);
}

function corpusLabel(
  summary: ReceiptHealthLedgerSummary | null,
  rootCause: string | undefined,
): string {
  const total = classifiedRootCauseTotal(summary);
  if (!rootCause || total === 0) return total > 0 ? `${total} classified` : "No corpus count";
  const count = summary?.by_preflight_root_cause?.[rootCause] ?? 0;
  return `${count}/${total} like this`;
}

const RECEIPT_MAP_SECTIONS: Array<{ key: string; label: string }> = [
  { key: "item_or_header", label: "items" },
  { key: "totals", label: "totals" },
  { key: "payment_summary", label: "payment" },
  { key: "tip_entry_area", label: "tip area" },
  { key: "void_discount", label: "void" },
  { key: "footer", label: "footer" },
];

function SectionMap({
  evidence,
}: {
  evidence: ReceiptHealthSectionEvidence | undefined;
}) {
  const issueSections = evidence?.issue_sections ?? {};
  const contextSections = evidence?.context_sections ?? {};
  const hasEvidence = Object.values(issueSections).some((count) => count > 0) ||
    Object.values(contextSections).some((count) => count > 0);

  return (
    <div className={styles.sectionMap}>
      {RECEIPT_MAP_SECTIONS.map((section) => {
        const issueCount = issueSections[section.key] ?? 0;
        const contextCount = contextSections[section.key] ?? 0;
        const isIssue = issueCount > 0;
        const isContext = !isIssue && contextCount > 0;

        return (
          <div
            key={section.key}
            className={`${styles.sectionMapRow} ${isIssue ? styles.sectionMapIssue : ""} ${isContext ? styles.sectionMapContext : ""}`}
            title={`${section.label}: ${isIssue ? `${issueCount} issue row${issueCount === 1 ? "" : "s"}` : isContext ? `${contextCount} nearby row${contextCount === 1 ? "" : "s"}` : "no evidence"}`}
          >
            <span className={styles.sectionMapLabel}>{section.label}</span>
            <span className={styles.sectionMapRule}>
              {isIssue || isContext ? (
                <span
                  className={styles.sectionMapMarker}
                  aria-label={isIssue ? `${section.label} issue evidence` : `${section.label} context evidence`}
                />
              ) : null}
            </span>
            <span className={styles.sectionMapCount}>
              {isIssue ? issueCount : contextCount || ""}
            </span>
          </div>
        );
      })}
      {!hasEvidence ? (
        <div className={styles.sectionMapEmpty}>-</div>
      ) : null}
    </div>
  );
}

function LineItemTokenMap({
  evidence,
}: {
  evidence: ReceiptHealthLineItemAmountEvidence | undefined;
}) {
  if (!evidence?.item_amount_row_count) return null;

  const metrics = [
    {
      key: "rows",
      value: evidence.item_amount_row_count ?? 0,
      label: "visual item amount rows",
      className: styles.lineItemMetricRows,
    },
    {
      key: "clean",
      value: evidence.clean_amount_row_count ?? 0,
      label: "clean price tokens",
      className: styles.lineItemMetricClean,
    },
    {
      key: "fragments",
      value: evidence.fragmented_amount_row_count ?? 0,
      label: "fragmented price tokens",
      className: styles.lineItemMetricFragment,
    },
    {
      key: "labeled",
      value: evidence.labeled_line_total_row_count ?? 0,
      label: "labeled LINE_TOTAL candidates",
      className: styles.lineItemMetricLabeled,
    },
  ];
  const maxValue = Math.max(...metrics.map((metric) => metric.value), 1);

  return (
    <div
      className={styles.lineItemTokenMap}
      aria-label="Line item price token evidence"
      title={`${evidence.item_amount_row_count ?? 0} item rows, ${evidence.clean_amount_row_count ?? 0} clean prices, ${evidence.fragmented_amount_row_count ?? 0} fragmented prices, ${evidence.labeled_line_total_row_count ?? 0} labeled candidates`}
    >
      {metrics.map((metric) => (
        <span
          key={metric.key}
          className={`${styles.lineItemMetric} ${metric.className}`}
          style={{ "--metric-width": `${Math.max(8, (metric.value / maxValue) * 100)}%` } as React.CSSProperties}
          title={`${metric.value} ${metric.label}`}
        >
          <span />
          <strong>{metric.value}</strong>
        </span>
      ))}
    </div>
  );
}

function ValidationChecks({
  checks,
  activeCheck,
  ledgerIssues,
  loadingLedgerIssues,
  muteExplanations = false,
  onSelectCheck,
}: {
  checks: ReceiptHealthCheck[];
  activeCheck: ReceiptHealthCheck;
  ledgerIssues: ReceiptHealthLedgerIssue[];
  loadingLedgerIssues: boolean;
  muteExplanations?: boolean;
  onSelectCheck: (checkId: CheckId) => void;
}) {
  const validationRows = checks.map((check) => {
    const issue = selectDisplayIssue(ledgerIssues, check.id);
    const explanation = validationRowExplanation(
      check,
      issue,
      loadingLedgerIssues,
    );
    return { check, explanation };
  });

  return (
    <div className={styles.validationStrip}>
      {validationRows.map(({ check, explanation }) => (
        <div
          key={check.id}
          className={styles.validationRow}
        >
          <button
            type="button"
            className={[
              styles.validationPill,
              activeCheck.id === check.id ? styles.validationPillActive : "",
              explanation ? styles.validationPillProblem : "",
            ].filter(Boolean).join(" ")}
            onClick={() => onSelectCheck(check.id)}
            title={`${CHECK_LABELS[check.id]}: ${validationOutcomeLabel(check.status)}`}
            aria-label={`${CHECK_LABELS[check.id]} ${validationOutcomeLabel(check.status)}`}
          >
            <span className={styles.validationLabel}>{CHECK_LABELS[check.id]}</span>
            <ValidationStatusIcon status={check.status} />
          </button>
          <ValidationReason explanation={explanation} muted={muteExplanations} />
        </div>
      ))}
    </div>
  );
}

function EvidenceSummary({
  lineItemEvidence,
  issueCount,
}: {
  lineItemEvidence: ReceiptHealthLineItemAmountEvidence | undefined;
  issueCount: number;
}) {
  if (lineItemEvidence?.item_amount_row_count) {
    return (
      <div className={styles.evidenceSummary}>
        <div>
          <strong>{lineItemEvidence.item_amount_row_count}</strong>
          <span>item price rows</span>
        </div>
        <div>
          <strong>{lineItemEvidence.fragmented_amount_row_count ?? 0}</strong>
          <span>fragmented prices</span>
        </div>
        <div>
          <strong>{lineItemEvidence.labeled_line_total_row_count ?? 0}</strong>
          <span>usable line total</span>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.evidenceSummary}>
      <div>
        <strong>{issueCount}</strong>
        <span>{issueCount === 1 ? "validation issue" : "validation issues"}</span>
      </div>
    </div>
  );
}

function EvidenceRows({
  sectionEvidence,
  lineItemEvidence,
  issueText,
  summary,
  route,
  corpus,
  children,
}: {
  sectionEvidence: ReceiptHealthSectionEvidence | undefined;
  lineItemEvidence: ReceiptHealthLineItemAmountEvidence | undefined;
  issueText: string;
  summary: string | undefined;
  route: string;
  corpus: string;
  children?: React.ReactNode;
}) {
  const rows = sectionEvidence?.issue_rows ?? [];
  const lineItemRows = lineItemEvidence?.rows ?? [];
  if (rows.length === 0 && lineItemRows.length === 0 && !summary && !issueText && !children) {
    return null;
  }

  return (
    <details className={styles.evidenceDisclosure}>
      <summary>Details</summary>
      <div className={styles.evidenceRowsCompact}>
        {children}
        {issueText ? (
          <div className={styles.evidenceTextLine}>{issueText}</div>
        ) : null}
        {summary ? (
          <div className={styles.evidenceTextLine}>{summary}</div>
        ) : null}
        <div className={styles.evidenceMetaLine}>
          <span>Route</span>
          <strong>{route}</strong>
        </div>
        <div className={styles.evidenceMetaLine}>
          <span>Corpus</span>
          <strong>{corpus}</strong>
        </div>
        <LineItemTokenMap evidence={lineItemEvidence} />
        <SectionMap evidence={sectionEvidence} />
        {lineItemRows.slice(0, 4).map((row) => (
          <div
            key={`line-item-${row.row_index}-${row.line_ids.join("-")}`}
            className={styles.evidenceRowCompact}
          >
            <span>prices</span>
            <span>{row.text}</span>
          </div>
        ))}
        {rows.slice(0, 3).map((row) => (
          <div
            key={`${row.row_index}-${row.line_ids.join("-")}`}
            className={styles.evidenceRowCompact}
          >
            <span>{sectionLabel(row.section)}</span>
            <span>{row.text}</span>
          </div>
        ))}
      </div>
    </details>
  );
}

function ScenarioExamples({
  receipts,
  currentIndex,
  onSelectReceipt,
}: {
  receipts: ReceiptHealthReceipt[];
  currentIndex: number;
  onSelectReceipt: (index: number) => void;
}) {
  const examples = FLOW_MOCK_SCENARIOS.map((scenario) => {
    const index = receipts.findIndex(
      (receipt) =>
        receipt.image_id === scenario.imageId &&
        receipt.receipt_id === scenario.receiptId,
    );
    if (index < 0) return null;
    return { ...scenario, index, receipt: receipts[index] };
  }).filter((example): example is (typeof FLOW_MOCK_SCENARIOS)[number] & {
    index: number;
    receipt: ReceiptHealthReceipt;
  } => Boolean(example));

  if (examples.length === 0) return null;

  return (
    <div className={styles.stateExamples}>
      <div className={styles.stateExamplesHeader}>Examples</div>
      <div className={styles.stateExampleButtons}>
        {examples.map((example) => (
          <button
            key={`${example.imageId}-${example.receiptId}`}
            type="button"
            className={`${styles.stateExampleButton} ${example.index === currentIndex ? styles.stateExampleActive : ""}`}
            onClick={() => onSelectReceipt(example.index)}
            aria-label={example.label}
            title={example.label}
          >
            <span className={`${styles.stateExampleDot} ${statusToneClass(example.receipt.overall_status)}`} />
          </button>
        ))}
      </div>
    </div>
  );
}

function DiagnosisRail({
  receipt,
  checks,
  activeCheck,
  ledgerIssue,
  ledgerIssues,
  ledgerSummary,
  loadingLedgerIssues,
  receipts,
  currentIndex,
  muteExplanations = false,
  onSelectCheck,
  onSelectReceipt,
}: {
  receipt: ReceiptHealthReceipt;
  checks: ReceiptHealthCheck[];
  activeCheck: ReceiptHealthCheck;
  ledgerIssue: ReceiptHealthLedgerIssue | null;
  ledgerIssues: ReceiptHealthLedgerIssue[];
  ledgerSummary: ReceiptHealthLedgerSummary | null;
  loadingLedgerIssues: boolean;
  receipts: ReceiptHealthReceipt[];
  currentIndex: number;
  muteExplanations?: boolean;
  onSelectCheck: (checkId: CheckId) => void;
  onSelectReceipt: (index: number) => void;
}) {
  const preflight = ledgerIssue?.preflight;
  const sectionEvidence = preflight?.evidence?.section_evidence;
  const lineItemEvidence = preflight?.evidence?.line_item_amount_evidence;
  const issueText = issueMessageForRail(receipt, activeCheck, ledgerIssue);
  const route = combinedRouteLabel(preflight, loadingLedgerIssues);
  const corpus = corpusLabel(ledgerSummary, preflight?.root_cause);
  const diagnosis = diagnosisCopy(
    receipt,
    activeCheck,
    preflight,
    loadingLedgerIssues,
  );
  const hasIssueContext =
    receipt.summary.issue_count > 0 ||
    activeCheck.status === "fail" ||
    activeCheck.status === "review";
  const showDebugDetails =
    Boolean(preflight) ||
    (hasIssueContext && !loadingLedgerIssues);

  return (
    <div className={styles.diagnosisRail}>
      <ValidationChecks
        checks={checks}
        activeCheck={activeCheck}
        ledgerIssues={ledgerIssues}
        loadingLedgerIssues={loadingLedgerIssues}
        muteExplanations={muteExplanations}
        onSelectCheck={onSelectCheck}
      />

      {showDebugDetails ? (
        <EvidenceRows
          sectionEvidence={sectionEvidence}
          lineItemEvidence={lineItemEvidence}
          issueText={issueText}
          summary={preflight?.summary}
          route={route}
          corpus={corpus}
        >
          <div className={styles.evidenceMetaLine}>
            <span>Receipt</span>
            <strong>{receiptTitle(receipt)}</strong>
          </div>
          <div className={styles.evidenceTextLine}>{diagnosis.body}</div>
          <EvidenceSummary
            lineItemEvidence={lineItemEvidence}
            issueCount={receipt.summary.issue_count}
          />
          <div className={styles.nextAction}>
            <span>Next</span>
            <strong>{diagnosis.action}</strong>
          </div>
          <ScenarioExamples
            receipts={receipts}
            currentIndex={currentIndex}
            onSelectReceipt={onSelectReceipt}
          />
        </EvidenceRows>
      ) : null}
    </div>
  );
}

function RootCauseVolume({
  summary,
  activeRootCause,
}: {
  summary: ReceiptHealthLedgerSummary | null;
  activeRootCause: string | undefined;
}) {
  const allEntries = sortedCountEntries(summary?.by_preflight_root_cause).filter(
    ([rootCause]) => rootCause !== "unclassified",
  );
  const entries = allEntries.slice(0, 5);
  if (entries.length === 0) return null;

  const maxCount = entries[0]?.[1] ?? 1;
  const classifiedTotal = allEntries.reduce((sum, [, count]) => sum + count, 0);

  return (
    <div className={styles.rootCauseVolume}>
      <div className={styles.volumeHeader}>
        <span>Root causes</span>
        <span>{classifiedTotal} classified</span>
      </div>
      <div className={styles.volumeRows}>
        {entries.map(([rootCause, count]) => {
          const width = maxCount > 0 ? (count / maxCount) * 100 : 0;
          const isActive = activeRootCause === rootCause;
          return (
            <div
              key={rootCause}
              className={`${styles.volumeRow} ${isActive ? styles.volumeRowActive : ""}`}
            >
              <span className={styles.volumeLabel}>{sectionLabel(rootCause)}</span>
              <span className={styles.volumeBarTrack}>
                <span
                  className={styles.volumeBar}
                  style={{ "--volume-width": `${width}%` } as React.CSSProperties}
                />
              </span>
              <span className={styles.volumeCount}>{count}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DecisionPath({
  receipt,
  activeCheck,
  ledgerIssue,
  loadingLedgerIssues,
}: {
  receipt: ReceiptHealthReceipt;
  activeCheck: ReceiptHealthCheck;
  ledgerIssue: ReceiptHealthLedgerIssue | null;
  loadingLedgerIssues: boolean;
}) {
  const preflight = ledgerIssue?.preflight;
  const sectionEvidence = preflight?.evidence?.section_evidence;
  const primaryIssue = receipt.primary_issues.find(
    (issue) => issue.check_id === activeCheck.id,
  ) ?? receipt.primary_issues[0];
  const issueMessage = compactText(
    ledgerIssue?.message ?? primaryIssue?.message,
    activeCheck.result,
  );
  const routeLabel = preflight
    ? (PREFLIGHT_LABELS[preflight.classification] ?? preflight.classification)
    : loadingLedgerIssues
      ? "Loading"
      : "No route";
  const rootCause = preflight?.root_cause
    ? sectionLabel(preflight.root_cause)
    : "No root cause";
  const sectionSummary = sectionEvidence
    ? compactText(
        [
          sectionEntries(sectionEvidence.issue_sections)
            .slice(0, 2)
            .map(([section]) => sectionLabel(section))
            .join(", "),
          sectionEntries(sectionEvidence.context_sections)
            .slice(0, 1)
            .map(([section]) => `near ${sectionLabel(section)}`)
            .join(", "),
        ]
          .filter(Boolean)
          .join("; "),
        "Section evidence attached",
      )
    : "No section trigger";
  const actionText = preflight?.is_automation_ready
    ? `${preflight.action_count} exact action${preflight.action_count === 1 ? "" : "s"}`
    : preflight
      ? "Held from writes"
      : "No stored action";

  return (
    <div className={styles.decisionPath}>
      <div className={styles.decisionHeader}>
        <span>{receiptTitle(receipt)}</span>
        <span>{CHECK_LABELS[activeCheck.id]}</span>
      </div>
      <div className={styles.decisionSteps}>
        <div className={styles.decisionStep}>
          <span className={styles.decisionStepIndex}>1</span>
          <span className={styles.decisionStepBody}>
            <span className={styles.decisionStepTitle}>Issue</span>
            <span className={styles.decisionStepText}>{issueMessage}</span>
          </span>
        </div>
        <div className={styles.decisionStep}>
          <span className={styles.decisionStepIndex}>2</span>
          <span className={styles.decisionStepBody}>
            <span className={styles.decisionStepTitle}>Section</span>
            <span className={styles.decisionStepText}>{sectionSummary}</span>
          </span>
        </div>
        <div className={styles.decisionStep}>
          <span className={styles.decisionStepIndex}>3</span>
          <span className={styles.decisionStepBody}>
            <span className={styles.decisionStepTitle}>Root cause</span>
            <span className={styles.decisionStepText}>{rootCause}</span>
          </span>
        </div>
        <div className={styles.decisionStep}>
          <span className={styles.decisionStepIndex}>4</span>
          <span className={styles.decisionStepBody}>
            <span className={styles.decisionStepTitle}>Route</span>
            <span className={styles.decisionStepText}>
              {routeLabel}
              {preflight?.lane ? ` · ${sectionLabel(preflight.lane)}` : ""}
            </span>
          </span>
        </div>
        <div className={styles.decisionStep}>
          <span className={styles.decisionStepIndex}>5</span>
          <span className={styles.decisionStepBody}>
            <span className={styles.decisionStepTitle}>Automation</span>
            <span className={styles.decisionStepText}>{actionText}</span>
          </span>
        </div>
      </div>
      {preflight?.summary ? (
        <div className={styles.decisionSummary}>{preflight.summary}</div>
      ) : null}
      <SectionEvidence evidence={sectionEvidence} />
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
            <SectionEvidence
              evidence={preflight?.evidence?.section_evidence}
            />
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
                  setReceiptImageFallback(event.currentTarget, fallback);
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
  formatSupport,
  suppressIntro = false,
  onImageUnavailable,
}: {
  receipt: ReceiptHealthReceipt;
  activeCheck: ReceiptHealthCheck;
  formatSupport: ImageFormatSupport;
  suppressIntro?: boolean;
  onImageUnavailable: (receipt: ReceiptHealthReceipt) => void;
}) {
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const [imageFailed, setImageFailed] = useState(false);

  const imageUrl = useMemo(() => {
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
    <div
      className={[
        styles.flowActiveReceipt,
        suppressIntro ? styles.flowActiveReceiptNoIntro : "",
      ].filter(Boolean).join(" ")}
    >
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
              if (setReceiptImageFallback(event.currentTarget, fallback)) {
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
  ledgerIssues,
  ledgerSummary,
  loadingLedgerIssues,
  receipts,
  currentIndex,
  muteExplanations = false,
  suppressIntro = false,
  onSelectCheck,
  onSelectReceipt,
}: {
  receipt: ReceiptHealthReceipt;
  checks: ReceiptHealthCheck[];
  activeCheck: ReceiptHealthCheck;
  ledgerIssues: ReceiptHealthLedgerIssue[];
  ledgerSummary: ReceiptHealthLedgerSummary | null;
  loadingLedgerIssues: boolean;
  receipts: ReceiptHealthReceipt[];
  currentIndex: number;
  muteExplanations?: boolean;
  suppressIntro?: boolean;
  onSelectCheck: (checkId: CheckId) => void;
  onSelectReceipt: (index: number) => void;
}) {
  const firstLedgerIssue = selectDisplayIssue(ledgerIssues);
  const activeLedgerIssue = selectDisplayIssue(ledgerIssues, activeCheck.id) ?? firstLedgerIssue;

  return (
    <aside
      className={[
        styles.flowEntityLegend,
        suppressIntro ? styles.flowEntityLegendNoIntro : "",
      ].filter(Boolean).join(" ")}
    >
      <DiagnosisRail
        receipt={receipt}
        checks={checks}
        activeCheck={activeCheck}
        ledgerIssue={activeLedgerIssue}
        ledgerIssues={ledgerIssues}
        ledgerSummary={ledgerSummary}
        loadingLedgerIssues={loadingLedgerIssues}
        receipts={receipts}
        currentIndex={currentIndex}
        muteExplanations={muteExplanations}
        onSelectCheck={onSelectCheck}
        onSelectReceipt={onSelectReceipt}
      />
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
                    setReceiptImageFallback(event.currentTarget, fallback);
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
  const { ref: lazyRef, inView: nearViewport } = useInView({
    triggerOnce: true,
    rootMargin: "400px",
  });
  const { ref: activeRef, inView } = useInView({
    threshold: 0.3,
    triggerOnce: false,
  });
  const setRefs = useCallback(
    (node?: Element | null) => {
      lazyRef(node);
      activeRef(node);
    },
    [activeRef, lazyRef],
  );

  const [receipts, setReceipts] = useState<ReceiptHealthReceipt[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [activeCheckId, setActiveCheckId] = useState<CheckId>("merchant_identity");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [findingIssue, setFindingIssue] = useState(false);
  const [ledgerIssues, setLedgerIssues] = useState<ReceiptHealthLedgerIssue[]>([]);
  const [ledgerSummary, setLedgerSummary] = useState<ReceiptHealthLedgerSummary | null>(null);
  const [transitionLedgerContext, setTransitionLedgerContext] = useState<LedgerContext | null>(null);
  const [loadingLedgerIssues, setLoadingLedgerIssues] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [totalCount, setTotalCount] = useState(0);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [transitionTargetIndex, setTransitionTargetIndex] = useState<number | null>(null);
  const [promotedReceiptKey, setPromotedReceiptKey] = useState<string | null>(null);
  const [unavailableImageKeys, setUnavailableImageKeys] = useState<Set<string>>(
    () => new Set(),
  );
  const fetchedInitial = useRef(false);
  const ledgerContextCacheRef = useRef<Map<string, LedgerContext>>(new Map());
  const ledgerContextRequestCacheRef = useRef<Map<string, Promise<LedgerContext>>>(new Map());
  const lastManualSelectionRef = useRef(0);
  const transitionTimerRef = useRef<number | null>(null);
  const transitionInFlightRef = useRef(false);
  const transitionRequestIdRef = useRef(0);
  const isMountedRef = useRef(true);
  const currentIndexRef = useRef(currentIndex);
  const receiptsRef = useRef(receipts);
  const formatSupport = useImageFormatSupport();

  usePreloadReceiptImages(
    receipts,
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
        try {
          const response = await api.fetchReceiptHealth(
            BATCH_SIZE,
            INITIAL_SEED,
            0,
            { imageId: scenario.imageId },
          );
          return response.receipts.find(
            (receipt) => receipt.receipt_id === scenario.receiptId,
          ) ?? null;
        } catch (err) {
          console.warn(
            `Skipping unavailable receipt health scenario ${scenario.label}:`,
            err,
          );
          return null;
        }
      }),
    );

    const scenarioReceipts = scenarioResults.filter(
      (receipt): receipt is ReceiptHealthReceipt => Boolean(receipt),
    );

    if (scenarioReceipts.length === 0) {
      return loadReceipts(0);
    }

    setTotalCount(scenarioReceipts.length);
    setHasMore(false);
    setReceipts(scenarioReceipts);
    return scenarioReceipts;
  }, [loadReceipts]);

  const fetchLedgerContext = useCallback(async (
    receipt: ReceiptHealthReceipt,
  ): Promise<LedgerContext> => {
    const contextKey = receiptKey(receipt);
    const cachedContext = ledgerContextCacheRef.current.get(contextKey);
    if (cachedContext) return cachedContext;

    const pendingContext = ledgerContextRequestCacheRef.current.get(contextKey);
    if (pendingContext) return pendingContext;

    const contextRequest = api.fetchReceiptHealthIssues({
      state: "all",
      imageId: receipt.image_id,
      receiptId: receipt.receipt_id,
      limit: 50,
    }).then((response) => {
      const nextContext = {
        issues: response.issues,
        summary: response.summary ?? null,
      };
      ledgerContextCacheRef.current.set(contextKey, nextContext);
      return nextContext;
    }).finally(() => {
      ledgerContextRequestCacheRef.current.delete(contextKey);
    });

    ledgerContextRequestCacheRef.current.set(contextKey, contextRequest);
    return contextRequest;
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
    currentIndexRef.current = currentIndex;
  }, [currentIndex]);

  useEffect(() => {
    receiptsRef.current = receipts;
  }, [receipts]);

  useEffect(() => {
    isMountedRef.current = true;

    return () => {
      isMountedRef.current = false;
      if (transitionTimerRef.current !== null) {
        window.clearTimeout(transitionTimerRef.current);
      }
      transitionRequestIdRef.current += 1;
      transitionInFlightRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!currentReceipt) {
      setLedgerIssues([]);
      setLedgerSummary(null);
      setLoadingLedgerIssues(false);
      return;
    }

    const cachedContext = ledgerContextCacheRef.current.get(receiptKey(currentReceipt));
    if (cachedContext) {
      setLedgerIssues(cachedContext.issues);
      setLedgerSummary(cachedContext.summary);
      setLoadingLedgerIssues(false);
      return;
    }

    let cancelled = false;
    setLoadingLedgerIssues(true);
    setLedgerIssues([]);
    setLedgerSummary(null);
    fetchLedgerContext(currentReceipt)
      .then((nextContext) => {
        if (!cancelled) {
          setLedgerIssues(nextContext.issues);
          setLedgerSummary(nextContext.summary);
        }
      })
      .catch((err) => {
        console.error("Failed to fetch receipt health issues:", err);
        if (!cancelled) {
          setLedgerIssues([]);
          setLedgerSummary(null);
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
  }, [currentReceipt, fetchLedgerContext]);

  const orderedChecks = useMemo(() => {
    return orderedChecksForReceipt(currentReceipt);
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

  const focusReceiptCheck = useCallback((receipt: ReceiptHealthReceipt | null | undefined) => {
    const nextCheck = preferredCheckIdForReceipt(receipt);
    if (nextCheck) {
      setActiveCheckId(nextCheck);
    }
  }, []);

  const selectReceipt = useCallback(async (index: number, options: { manual?: boolean } = {}) => {
    const receipt = receiptsRef.current[index];
    if (!receipt || !formatSupport) return;

    if (options.manual ?? true) {
      lastManualSelectionRef.current = Date.now();
    }

    if (index === currentIndexRef.current) {
      focusReceiptCheck(receipt);
      return;
    }

    if (transitionTimerRef.current !== null || transitionInFlightRef.current) return;

    const transitionRequestId = transitionRequestIdRef.current + 1;
    transitionRequestIdRef.current = transitionRequestId;
    transitionInFlightRef.current = true;
    setTransitionLedgerContext(null);
    setTransitionTargetIndex(null);

    let prefetchedLedgerContext =
      ledgerContextCacheRef.current.get(receiptKey(receipt)) ?? null;
    const ledgerContextPromise = fetchLedgerContext(receipt)
      .catch((err): LedgerContext => {
        console.warn("Failed to prefetch receipt health issues:", err);
        return emptyLedgerContext;
      })
      .then((nextContext) => {
        prefetchedLedgerContext = nextContext;
        if (
          isMountedRef.current &&
          transitionRequestIdRef.current === transitionRequestId
        ) {
          setTransitionLedgerContext(nextContext);
        }
        return nextContext;
      });
    await preloadReceiptImageForTransition(receipt, formatSupport);

    if (
      !isMountedRef.current ||
      transitionRequestIdRef.current !== transitionRequestId
    ) {
      transitionInFlightRef.current = false;
      return;
    }

    setPromotedReceiptKey(null);
    setTransitionLedgerContext(prefetchedLedgerContext ?? emptyLedgerContext);
    setTransitionTargetIndex(index);
    setIsTransitioning(true);

    transitionTimerRef.current = window.setTimeout(() => {
      const nextReceiptKey = receiptKey(receipt);
      if (prefetchedLedgerContext) {
        setLedgerIssues(prefetchedLedgerContext.issues);
        setLedgerSummary(prefetchedLedgerContext.summary);
        setLoadingLedgerIssues(false);
      } else {
        setLedgerIssues([]);
        setLedgerSummary(null);
        setLoadingLedgerIssues(true);
      }
      setCurrentIndex(index);
      focusReceiptCheck(receipt);
      setPromotedReceiptKey(nextReceiptKey);
      setIsTransitioning(false);
      setTransitionTargetIndex(null);
      setTransitionLedgerContext(null);
      transitionRequestIdRef.current += 1;
      transitionTimerRef.current = null;
      transitionInFlightRef.current = false;
    }, TRANSITION_DURATION_MS);
    void ledgerContextPromise;
  }, [fetchLedgerContext, focusReceiptCheck, formatSupport]);

  useEffect(() => {
    if (
      !inView ||
      loading ||
      receipts.length < 2 ||
      findingIssue ||
      loadingMore ||
      isTransitioning
    ) return;

    const timer = window.setInterval(() => {
      if (Date.now() - lastManualSelectionRef.current < MANUAL_ROTATE_PAUSE_MS) {
        return;
      }

      const nextIndex = (currentIndexRef.current + 1) % receiptsRef.current.length;
      selectReceipt(nextIndex, { manual: false });
    }, AUTO_ROTATE_MS);

    return () => window.clearInterval(timer);
  }, [
    findingIssue,
    inView,
    isTransitioning,
    loading,
    loadingMore,
    receipts,
    selectReceipt,
  ]);

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
      selectReceipt(replacementIndex, { manual: false });
    }
  }, [
    currentIndex,
    currentReceipt,
    receipts,
    selectReceipt,
    unavailableImageKeys,
  ]);

  const goPrevious = useCallback(() => {
    selectReceipt(Math.max(0, currentIndex - 1));
  }, [currentIndex, selectReceipt]);

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
        lastManualSelectionRef.current = Date.now();
        setCurrentIndex(previousLength);
        focusReceiptCheck(loaded[0]);
      }
    } catch (err) {
      console.error("Failed to fetch more receipt health data:", err);
      setError(err instanceof Error ? err.message : "Failed to load more receipts");
    } finally {
      setLoadingMore(false);
    }
  }, [
    currentIndex,
    focusReceiptCheck,
    hasMore,
    loadReceipts,
    loadingMore,
    receipts.length,
    selectReceipt,
  ]);

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
          lastManualSelectionRef.current = Date.now();
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

  const getFlyingReceipt = useCallback(
    (items: ReceiptHealthReceipt[]) => {
      if (transitionTargetIndex === null) return null;
      return items[transitionTargetIndex] ?? null;
    },
    [transitionTargetIndex],
  );

  const { flyingItem, showFlying } = useFlyingReceipt(
    isTransitioning,
    receipts,
    currentIndex,
    getFlyingReceipt,
  );

  const flyingElement = useMemo(() => {
    if (!showFlying || !flyingItem || !formatSupport) return null;

    const imageUrl = getBestImageUrl(flyingItem, formatSupport, "medium");
    if (!imageUrl) return null;

    const { displayWidth, displayHeight } = receiptDisplaySize(flyingItem);
    const receiptId = `${flyingItem.image_id}_${flyingItem.receipt_id}`;

    return (
      <FlyingReceipt
        key={`receipt-health-flying-${receiptKey(flyingItem)}`}
        imageUrl={imageUrl}
        displayWidth={displayWidth}
        displayHeight={displayHeight}
        receiptId={receiptId}
        onImageError={(event) => {
          const fallback = getJpegFallbackUrl(flyingItem);
          setReceiptImageFallback(event.currentTarget, fallback);
        }}
      />
    );
  }, [flyingItem, formatSupport, showFlying]);

  const transitionTargetReceipt =
    transitionTargetIndex === null ? null : receipts[transitionTargetIndex] ?? null;
  const transitionTargetChecks = useMemo(
    () => orderedChecksForReceipt(transitionTargetReceipt),
    [transitionTargetReceipt],
  );
  const transitionTargetCheck = useMemo(() => {
    if (!transitionTargetReceipt) return null;
    const preferredCheckId = preferredCheckIdForReceipt(transitionTargetReceipt);
    return (
      transitionTargetChecks.find(
        (check) => check.id === preferredCheckId,
      ) ??
      transitionTargetChecks[0] ??
      null
    );
  }, [transitionTargetChecks, transitionTargetReceipt]);

  if (loading || !formatSupport) {
    return (
      <div
        ref={setRefs}
        className={`${styles.container} ${styles.flowMockContainer}`}
        data-testid="receipt-health-explorer"
      >
        <ReceiptFlowLoadingShell
          variant="health"
          layoutVars={FLOW_LAYOUT_VARS}
        />
      </div>
    );
  }

  if (error || !currentReceipt || !activeCheck) {
    return (
      <div
        ref={setRefs}
        className={`${styles.container} ${styles.flowMockContainer}`}
        data-testid="receipt-health-explorer"
      >
        <ReceiptFlowLoadingShell
          variant="health"
          layoutVars={FLOW_LAYOUT_VARS}
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
      ref={setRefs}
      className={`${styles.container} ${styles.flowMockContainer}`}
      data-testid="receipt-health-explorer"
    >
      <ReceiptFlowShell
        layoutVars={FLOW_LAYOUT_VARS}
        isTransitioning={isTransitioning}
        stabilizeLegend
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
            key={receiptKey(currentReceipt)}
            receipt={currentReceipt}
            activeCheck={activeCheck}
            formatSupport={formatSupport}
            suppressIntro={promotedReceiptKey === receiptKey(currentReceipt)}
            onImageUnavailable={handleImageUnavailable}
          />
        }
        flying={flyingElement}
        next={
          isTransitioning && transitionTargetReceipt && transitionTargetCheck ? (
            <ReceiptHealthFlowReceipt
              key={`next-${receiptKey(transitionTargetReceipt)}`}
              receipt={transitionTargetReceipt}
              activeCheck={transitionTargetCheck}
              formatSupport={formatSupport}
              onImageUnavailable={handleImageUnavailable}
            />
          ) : null
        }
        legend={
          <ReceiptHealthFlowLegend
            receipt={currentReceipt}
            checks={orderedChecks}
            activeCheck={activeCheck}
            ledgerIssues={ledgerIssues}
            ledgerSummary={ledgerSummary}
            loadingLedgerIssues={loadingLedgerIssues}
            receipts={receipts}
            currentIndex={currentIndex}
            muteExplanations={isTransitioning}
            suppressIntro={promotedReceiptKey === receiptKey(currentReceipt)}
            onSelectCheck={setActiveCheckId}
            onSelectReceipt={selectReceipt}
          />
        }
        nextLegend={
          isTransitioning && transitionTargetReceipt && transitionTargetCheck ? (
            <ReceiptHealthFlowLegend
              receipt={transitionTargetReceipt}
              checks={transitionTargetChecks}
              activeCheck={transitionTargetCheck}
              ledgerIssues={transitionLedgerContext?.issues ?? []}
              ledgerSummary={transitionLedgerContext?.summary ?? null}
              loadingLedgerIssues={!transitionLedgerContext}
              receipts={receipts}
              currentIndex={transitionTargetIndex ?? currentIndex}
              muteExplanations={false}
              onSelectCheck={setActiveCheckId}
              onSelectReceipt={selectReceipt}
            />
          ) : null
        }
      />
    </div>
  );
}
