// Shapes served by portfolio/dev-harness/validation_shim.py (dev only).
import {
  LineItemDecodeImage,
  LineItemDecodeLine,
  LineItemReconciliationStatus,
} from "../../../types/api";

export type ReviewVerdict =
  | "confirm"
  | "flag"
  | "approve-fix"
  | "golden"
  | "audit-agree"
  | "audit-disagree";
export type OverlayMode = "sections" | "items" | "both";

/** The three things a human still does in the agentic-first loop. */
export type HarnessScreen = "escalation" | "digest" | "audit";

/** Scout-agent evidence: a bare sentence, or a labelled figure. */
export type DossierEvidence =
  | string
  | {
      label?: string | null;
      detail?: string | null;
      value?: string | number | null;
    };

export interface DossierDryRun {
  before_delta: number | null;
  after_delta: number | null;
  before_status: string | null;
  after_status: string | null;
}

export interface DossierProposal {
  /** v2 files name no tool — there is one write path, so the shim labels it. */
  tool: string | null;
  args: Record<string, unknown>;
  /** Null when the scout proposed a fix it never simulated. */
  dry_run: DossierDryRun | null;
  /** The real guard accepted this proposal in a dry run. */
  verified?: boolean;
  contiguous?: boolean;
  vision_products_confirmed?: boolean;
}

/**
 * Pre-session analysis written to .dev-harness/dossiers/ by a read-only
 * agent. A dossier that cannot justify a proposal abstains instead.
 */
export interface ReceiptDossier {
  /** Dossier v2's `mode`: the A–J taxonomy id, letter prefix = class. */
  failure_mode: string | null;
  diagnosis: string;
  evidence: DossierEvidence[];
  proposal: DossierProposal | null;
  abstain_reason: string | null;
  verdict_recommendation: string | null;
  /** v2 grades confidence high/medium/low, not numerically. */
  confidence: string | null;
  signals_concurring: string[];
  generated_at: string | null;
  author: string | null;
  source: string;
  /** Set by /audit: the agent's conclusions have been stripped out. */
  blind?: boolean;
}

export interface ValidationSummary {
  subtotal: number | null;
  grand_total: number | null;
  tax: number | null;
  baseline: number | null;
  merchant_name: string | null;
  tender_class: string | null;
  card_network: string | null;
  card_last4: string | null;
  ledger: string | null;
  bank_amount: number | null;
  bank_match_confidence: number | null;
}

export interface ValidationItem {
  item_index: number;
  name: string;
  price: number;
  quantity: number | null;
  unit_price: number | null;
  is_discount: boolean;
  line_ids: number[];
  name_quality: string | null;
  reconciliation_status: LineItemReconciliationStatus | null;
  extractor_version: string | null;
}

export interface ValidationSection {
  section_type: string;
  line_ids: number[];
  validation_status: string;
}

export interface ReviewEntry {
  image_id: string;
  receipt_id: number;
  verdict: ReviewVerdict;
  note: string;
  /** A–J failure-mode letter, or a client-side hint code. */
  reason?: string | null;
  /** Which OCR rows the verdict is about. */
  line_ids?: number[];
  merchant: string;
  status: string;
  delta: number | null;
  author: string;
  ts: string;
}

export interface ValidationReceipt {
  image_id: string;
  receipt_id: number;
  merchant_name: string;
  item_count: number;
  items: ValidationItem[];
  items_sum: number;
  delta: number | null;
  reconciliation_status: LineItemReconciliationStatus | null;
  items_section_line_ids: number[] | null;
  items_section_status: string | null;
  image: LineItemDecodeImage | null;
  lines: LineItemDecodeLine[];
  sections: ValidationSection[];
  summary: ValidationSummary | null;
  dossier: ReceiptDossier | null;
  dossier_error: string | null;
  reviews: ReviewEntry[];
}

export interface WorklistRow {
  image_id: string;
  receipt_id: number;
  merchant: string;
  status: LineItemReconciliationStatus;
  items: number;
  items_sum: number;
  baseline: number | null;
  subtotal: number | null;
  grand_total: number | null;
  tax: number | null;
  delta: number | null;
  tender_class: string | null;
  card_network: string | null;
  card_last4: string | null;
  ledger: string | null;
  bank_amount: number | null;
  bank_match_confidence: number | null;
}

export interface WorklistResponse {
  merchant?: string;
  status?: string;
  /** Set when the rows came from a curated queue file, not the filters. */
  queue?: string | null;
  queue_description?: string | null;
  /** Queue ids the index does not know about — a queue is never silently short. */
  missing?: { image_id: string; receipt_id: number }[];
  matching: number;
  receipts: WorklistRow[];
  built_at: string;
}

export interface QueueSummary {
  name: string;
  count: number;
  description: string | null;
  error: string | null;
}

export interface QueuesResponse {
  queues: QueueSummary[];
  dir: string;
}

/** Optional provenance attached to a verdict. */
export interface ReviewExtras {
  reason?: string | null;
  line_ids?: number[];
}

export interface ReviewLogResponse {
  entries: ReviewEntry[];
  log: string;
}

/* --- Batch digest: the T1 tier, approved a group at a time --- */

export interface DigestReceiptRef {
  image_id: string;
  receipt_id: number;
  delta: number | null;
  merchant: string | null;
  /** Why the adjudicator routed this receipt here. */
  reason: string | null;
  golden: boolean;
}

/**
 * One merchant × failure-mode group from the adjudicated pass. Approving it
 * is the human sign-off the writer waits on, so a group is the smallest unit
 * the reviewer ever says yes to.
 */
export interface DigestGroup {
  group_id: string;
  merchant: string;
  failure_mode: string;
  /** The tool the writer would run for every receipt in the group. */
  action: string | null;
  /** Golden candidates ratchet the CI floors; they are never routine. */
  golden_candidate: boolean;
  count: number;
  net_delta: number | null;
  receipts: DigestReceiptRef[];
  thumbnails: string[];
  approved: boolean;
  /** True when a failed blind audit froze this failure mode. */
  frozen: boolean;
}

export interface DigestResponse {
  pass_id: string | null;
  groups: DigestGroup[];
  passes: string[];
  frozen: string[];
  generated_at: string | null;
  source: string | null;
  warning?: string | null;
  error?: string | null;
}

export interface ApproveResponse {
  ok: true;
  already: boolean;
  pass_id: string;
  group_id: string;
  approvals: number;
  path: string;
}

/* --- Audit deck: blind re-review of the auto-applied tier --- */

export interface AuditSampleRef {
  image_id: string;
  receipt_id: number;
  merchant: string | null;
  reviewed: boolean;
}

export interface AuditDeckResponse {
  pass_id: string | null;
  size: number;
  /** How many auto-verdicts the sample was drawn from. */
  total_auto: number;
  sample: AuditSampleRef[];
  frozen: string[];
  warning?: string | null;
  error?: string | null;
}

/** A sampled receipt with the agent's conclusions withheld. */
export interface AuditReceipt extends ValidationReceipt {
  pass_id: string;
  blind: true;
}

/** What the agent had concluded, released only after the human commits. */
export interface AuditRevealed {
  tier: string | null;
  /** The adjudicator's routing reason, e.g. "auto-extension". */
  reason: string | null;
  failure_mode: string | null;
  diagnosis: string | null;
  verdict_recommendation: string | null;
  confidence: string | null;
  signals_concurring: string[];
  proposal: DossierProposal | null;
  abstain_reason: string | null;
}

export interface AuditReviewResponse {
  entry: ReviewEntry;
  revealed: AuditRevealed | null;
  /** Markers this disagreement wrote: the audited tier and its class. */
  freeze_written: string[];
  frozen: string[];
}
