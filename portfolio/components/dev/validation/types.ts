// Shapes served by portfolio/dev-harness/validation_shim.py (dev only).
import {
  LineItemDecodeImage,
  LineItemDecodeLine,
  LineItemReconciliationStatus,
} from "../../../types/api";

export type ReviewVerdict = "confirm" | "flag" | "resolved";

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
  image: LineItemDecodeImage;
  lines: LineItemDecodeLine[];
  sections: ValidationSection[];
  summary: ValidationSummary | null;
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
  merchant: string;
  status: string;
  matching: number;
  receipts: WorklistRow[];
  built_at: string;
}

export type StatusFilter =
  "failures" | "all" | "mismatch" | "near" | "match" | "no-baseline";
