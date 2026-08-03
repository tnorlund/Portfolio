// Shapes served by portfolio/dev-harness/validation_shim.py (dev only).
import {
  LineItemDecodeImage,
  LineItemDecodeLine,
  LineItemReconciliationStatus,
} from "../../../types/api";

export type ReviewVerdict = "confirm" | "flag" | "approve-fix" | "golden";
export type OverlayMode = "sections" | "items" | "both";

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
  tool: string;
  args: Record<string, unknown>;
  /** Null when the scout proposed a fix it never simulated. */
  dry_run: DossierDryRun | null;
}

/**
 * Pre-session analysis written to .dev-harness/dossiers/ by a read-only
 * agent. A dossier that cannot justify a proposal abstains instead.
 */
export interface ReceiptDossier {
  failure_mode: string | null;
  diagnosis: string;
  evidence: DossierEvidence[];
  proposal: DossierProposal | null;
  abstain_reason: string | null;
  generated_at: string | null;
  author: string | null;
  source: string;
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

export interface MerchantRow {
  name: string;
  receipts: number;
  match: number;
  near: number;
  mismatch: number;
  "no-baseline": number;
  with_bank: number;
  match_rate: number;
}

export interface MerchantsResponse {
  merchants: MerchantRow[];
  totals: Record<string, number>;
  receipts: number;
  built_at: string;
  table: string;
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

export type StatusFilter =
  "failures" | "all" | "mismatch" | "near" | "match" | "no-baseline";
