// Upload Progress Tracking Types

export interface UploadReceiptProgress {
  receipt_id: number;
  merchant_found: boolean;
  merchant_name: string | null;
  total_labels: number;
  validated_labels: number;
}

export interface UploadStatusResponse {
  image_id: string;
  ocr_status: "PENDING" | "COMPLETED" | "FAILED";
  processing_stage: string | null;
  receipt_count: number;
  receipts: UploadReceiptProgress[];
}

export interface LabelValidationCountResponse {
  [key: string]: {
    [key: string]: number;
  };
}

export interface LabelValidationStatusCounts {
  VALID: number;
  INVALID: number;
  PENDING: number;
  NEEDS_REVIEW: number;
  NONE: number;
  total: number;
}

export interface LabelValidationKeyframe {
  progress: number;
  timestamp: string;
  records_processed: number;
  labels: {
    [labelName: string]: LabelValidationStatusCounts;
  };
}

export interface LabelValidationTimelineResponse {
  generated_at: string;
  total_records: number;
  keyframes: LabelValidationKeyframe[];
}

export interface ImageDetailsApiResponse {
  image: Image;
  lines: Line[];
  receipts: Receipt[];
}

export interface CachedImageDetailsResponse {
  images: ImageDetailsApiResponse[];
  cached_at: string;
}

export interface ImageCountApiResponse {
  count: number;
}

export interface ReaderSummaryRequest {
  page_path: string;
  analytics_event_id: string;
  time_to_bottom_ms: number;
  active_scroll_ms: number;
  page_height: number;
  scrollable_pixels: number;
  screens_per_minute: number;
  quick_jump: boolean;
}

export interface ReaderSummaryBaseline {
  sampleSize: number;
  averageTimeToBottomMs: number | null;
  averageActiveScrollMs?: number | null;
  averageScreensPerMinute?: number | null;
  updatedAt?: string | null;
}

export interface ReaderSummaryResponse {
  accepted: boolean;
  counted: boolean;
  quickJump: boolean;
  pagePath: string;
  minimumSampleSize: number;
  comparison: ReaderSummaryBaseline & {
    readerDeltaPercent: number | null;
  };
  aggregate: ReaderSummaryBaseline;
}

export interface ReceiptApiResponse {
  receipts: Receipt[];
  lastEvaluatedKey?: any;
}

export type MerchantCountsResponse = Array<{
  [merchantName: string]: number;
}>;

export interface ReceiptsApiResponse {
  receipts: Receipt[];
  lastEvaluatedKey?: any;
}

export interface ImagesApiResponse {
  images: Image[];
  lastEvaluatedKey?: any;
}

export interface Point {
  x: number;
  y: number;
}

export interface AddressBoundingBox {
  tl: Point; // top-left
  tr: Point; // top-right
  bl: Point; // bottom-left
  br: Point; // bottom-right
}

export interface Image {
  image_id: string;
  width: number;
  height: number;
  timestamp_added: string;
  raw_s3_bucket: string;
  raw_s3_key: string;
  sha256: string;
  cdn_s3_bucket: string;
  cdn_s3_key: string;
  cdn_webp_s3_key?: string;
  cdn_avif_s3_key?: string;
  // Thumbnail versions
  cdn_thumbnail_s3_key?: string;
  cdn_thumbnail_webp_s3_key?: string;
  cdn_thumbnail_avif_s3_key?: string;
  // Small versions
  cdn_small_s3_key?: string;
  cdn_small_webp_s3_key?: string;
  cdn_small_avif_s3_key?: string;
  // Medium versions
  cdn_medium_s3_key?: string;
  cdn_medium_webp_s3_key?: string;
  cdn_medium_avif_s3_key?: string;
  image_type: string;
}

export interface Line {
  image_id: string;
  line_id: number;
  text: string;
  bounding_box: BoundingBox;
  top_left: Point;
  top_right: Point;
  bottom_left: Point;
  bottom_right: Point;
  angle_degrees: number;
  angle_radians: number;
  confidence: number;
}

export interface Word {
  image_id: string;
  line_id: number;
  word_id: number;
  text: string;
  bounding_box: BoundingBox;
  top_left: Point;
  top_right: Point;
  bottom_left: Point;
  bottom_right: Point;
  angle_degrees: number;
  angle_radians: number;
  confidence: number;
}

export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ReceiptWord {
  receipt_word_id: number;
  receipt_id: number;
  line_id: number;
  word_id: number;
  text: string;
  bounding_box: BoundingBox;
  top_left: Point;
  top_right: Point;
  bottom_left: Point;
  bottom_right: Point;
  angle_degrees: number;
  angle_radians: number;
  confidence: number;
}

export interface Receipt {
  receipt_id: number;
  image_id: string;
  width: number;
  height: number;
  timestamp_added: string;
  raw_s3_bucket: string;
  raw_s3_key: string;
  top_left: Point;
  top_right: Point;
  bottom_left: Point;
  bottom_right: Point;
  sha256: string;
  cdn_s3_bucket: string;
  cdn_s3_key: string;
  cdn_webp_s3_key?: string;
  cdn_avif_s3_key?: string;
  // Thumbnail versions
  cdn_thumbnail_s3_key?: string;
  cdn_thumbnail_webp_s3_key?: string;
  cdn_thumbnail_avif_s3_key?: string;
  // Small versions
  cdn_small_s3_key?: string;
  cdn_small_webp_s3_key?: string;
  cdn_small_avif_s3_key?: string;
  // Medium versions
  cdn_medium_s3_key?: string;
  cdn_medium_webp_s3_key?: string;
  cdn_medium_avif_s3_key?: string;
}

export interface ReceiptWordLabel {
  image_id: string;
  receipt_id: number;
  line_id: number;
  word_id: number;
  label: string;
  reasoning?: string;
  timestamp_added: string;
  validation_status?: string;
  label_proposed_by?: string;
  label_consolidated_from?: string;
}

export interface RandomReceiptDetailsResponse {
  receipt: Receipt;
  words: ReceiptWord[];
  labels: ReceiptWordLabel[];
}

export interface AddressSimilarityResponse {
  original: {
    receipt: Receipt;
    lines: Line[];
    words: Word[];
    labels: ReceiptWordLabel[];
    bbox?: AddressBoundingBox;
    address_text?: string;
    selected_group?: number[];
  };
  similar: Array<{
    receipt: Receipt;
    lines: Line[];
    words: Word[];
    labels: ReceiptWordLabel[];
    similarity_distance: number;
    bbox?: AddressBoundingBox;
    address_text?: string;
  }>;
  cached_at: string;
}

export interface WordSimilarityResponse {
  query_word: string;
  original: {
    receipt: Receipt;
    lines: Line[];
    words: Word[];
    labels: ReceiptWordLabel[];
    target_word: Word | null;
    bbox?: AddressBoundingBox;
  };
  similar: Array<{
    receipt: Receipt;
    lines: Line[];
    words: Word[];
    labels: ReceiptWordLabel[];
    target_word: Word;
    similarity_distance: number;
    bbox?: AddressBoundingBox;
  }>;
  cached_at: string;
}

// Milk product similarity response (line-based search)
export interface MilkSummaryRow {
  merchant: string;
  product: string;
  size: string;
  count: number;
  avg_price: number | null;
  total: number | null;
  receipts: Array<{ image_id: string; receipt_id: number }>;
}

export interface MilkReceiptData {
  image_id: string;
  receipt_id: number;
  product: string;
  merchant: string;
  price: string | null;
  size: string;
  line_id: number;
  receipt: Receipt;
  /**
   * Per-receipt line list. The frontend never reads this — the cropped
   * image is built from `receipt` + `bbox`. Cache generator no longer
   * emits it (saves ~95% of payload). Kept optional only so older cached
   * responses still type-check during rollout.
   */
  lines?: Line[];
  bbox: AddressBoundingBox | null;
}

export interface MilkSimilarityTiming {
  // S3 mode (legacy) - present when use_chroma_cloud is false
  s3_download_ms?: number;
  // Chroma Cloud mode - present when use_chroma_cloud is true
  cloud_connect_ms?: number;
  use_chroma_cloud?: boolean;
  // Common fields
  chromadb_init_ms: number;
  chromadb_fetch_all_ms: number;
  filter_lines_ms: number;
  dynamo_fetch_total_ms: number;
  total_ms: number;
  parallel_workers: number;
  dynamo_details?: {
    avg_ms: number;
    min_ms: number;
    max_ms: number;
    count: number;
    sequential_ms: number;
    speedup: number;
  };
  visual_line_assembly?: {
    avg_ms: number;
    min_ms: number;
    max_ms: number;
  };
}

export interface MilkSimilarityResponse {
  query_word: string;
  total_receipts: number;
  total_items: number;
  summary_table: MilkSummaryRow[];
  receipts: MilkReceiptData[];
  cached_at: string;
  timing?: MilkSimilarityTiming;
  grand_total?: number;
  commentary?: string;
}

export interface TrainingMetricsEpoch {
  epoch: number;
  is_best: boolean;
  metrics: {
    val_f1: number;
    val_precision?: number;
    val_recall?: number;
    eval_loss?: number;
    train_loss?: number;
    learning_rate?: number;
  };
  confusion_matrix?: {
    labels: string[];
    matrix: number[][];
  };
  per_label: Record<
    string,
    {
      f1: number;
      precision: number;
      recall: number;
      support: number;
    }
  >;
}

export interface DatasetMetrics {
  num_train_samples?: number;
  num_val_samples?: number;
  o_entity_ratio_train?: number;
  o_entity_ratio_val?: number;
  random_seed?: number;
  num_train_receipts?: number;
  num_val_receipts?: number;
  synthetic_train_examples?: number;
  synthetic_candidates_seen?: number;
  synthetic_candidates_accepted?: number;
  synthetic_candidates_rejected?: number;
  synthetic_rejection_reasons?: Record<string, number>;
}

export interface TrainingSynthesisCatalogItem {
  product_text?: string;
  category?: string;
  observed_count?: number;
}

export interface TrainingSynthesisReceiptPreviewLine {
  line_number?: number | null;
  text?: string;
  role?: string | null;
  synthetic_insert?: boolean;
  modified_labels?: string[];
}

export interface TrainingSynthesisReceiptPreview {
  coordinate_system?: string | null;
  line_count?: number | null;
  token_count?: number | null;
  truncated?: boolean;
  text?: string;
  lines?: TrainingSynthesisReceiptPreviewLine[];
}

export interface TrainingSynthesisStructureEvidence {
  score?: number | null;
  nearest_real_receipt_key?: string | null;
  components?: Record<string, number>;
  shape_deltas?: {
    token_count_delta?: number | null;
    line_count_delta?: number | null;
    line_item_count_delta?: number | null;
    line_step_delta?: number | null;
    line_total_x_delta?: number | null;
  };
  match_summary?: {
    matched_components?: string[];
    weak_components?: string[];
    shape_checks?: string[];
  };
  real_baseline_comparison?: {
    baseline_receipt_count?: number | null;
    baseline_pair_count?: number | null;
    candidate_score?: number | null;
    baseline_avg?: number | null;
    baseline_min?: number | null;
    baseline_max?: number | null;
    within_real_score_range?: boolean | null;
    delta_from_avg?: number | null;
    delta_from_min?: number | null;
  };
}

export interface TrainingSynthesisLayoutIntegrityEvidence {
  score?: number | null;
  passed?: boolean | null;
  line_count?: number | null;
  word_count?: number | null;
  overlap_pair_count?: number | null;
  out_of_bounds_word_count?: number | null;
  invalid_word_box_count?: number | null;
  line_order_valid?: boolean | null;
  overlap_examples?: Array<{
    left_text?: string | null;
    right_text?: string | null;
    left_line_id?: string | number | null;
    right_line_id?: string | number | null;
  }>;
  out_of_bounds_examples?: Array<Record<string, unknown>>;
  invalid_word_examples?: Array<Record<string, unknown>>;
}

export interface TrainingSynthesisAccuracyEvidence {
  operation?: string | null;
  checks?: string[];
  changed_text?: string | null;
  label?: string | null;
  old_text?: string | null;
  new_text?: string | null;
  category?: string | null;
  tax_delta?: string | null;
  catalog_grounding?: {
    product_observed_count?: number | null;
    product_seen_receipt_count?: number | null;
    product_seen_outside_base_count?: number | null;
    product_seen_outside_base?: string[];
    category?: string | null;
    category_seen_count?: number | null;
    category_heading_seen_count?: number | null;
    category_seen_in_receipts?: string[];
  };
  category_placement?: {
    category?: string | null;
    insert_y?: number | null;
    shifted_lower_lines_by?: number | null;
    selection_reason?: string | null;
    base_receipt_has_category?: boolean;
    category_seen_count?: number | null;
    category_heading_seen_count?: number | null;
    category_alignment?: string | null;
  };
  layout_integrity?: TrainingSynthesisLayoutIntegrityEvidence;
  structure_similarity?: TrainingSynthesisStructureEvidence;
}

export interface TrainingSynthesisCandidateExample {
  candidate_id?: string;
  merchant_name?: string;
  source?: string;
  operation?: string;
  actual_label?: string | null;
  predicted_label?: string | null;
  item_text?: string | null;
  category?: string | null;
  line_total?: string | null;
  seen_in_other_receipt?: boolean | null;
  field_label?: string | null;
  old_text?: string | null;
  new_text?: string | null;
  field_format?: string | null;
  field_observed_count?: number | null;
  evidence_receipts?: string[];
  structure_similarity?: number | null;
  nearest_real_receipt_key?: string | null;
  candidate_quality?: TrainingSynthesisCandidateQuality;
  selection_evidence?: TrainingSynthesisSelectionEvidence;
  receipt_preview?: TrainingSynthesisReceiptPreview;
  accuracy_evidence?: TrainingSynthesisAccuracyEvidence;
}

export interface TrainingSynthesisMerchantReadiness {
  merchant_name?: string;
  status?: string | null;
  score?: number | null;
  supported_operations?: string[];
  candidate_capacity?: number | null;
  catalog_item_count?: number | null;
  category_count?: number | null;
  grounded_add_item_candidate_count?: number | null;
  removable_item_candidate_count?: number | null;
  blockers?: string[];
  limitations?: string[];
}

export interface TrainingSynthesisSourceQualityMerchant {
  merchant_name?: string | null;
  status?: string | null;
  receipt_count?: number | null;
  receipts_with_lines?: number | null;
  receipts_with_words?: number | null;
  receipts_with_labels?: number | null;
  receipts_with_merchant_name_label?: number | null;
  receipts_with_line_item_labels?: number | null;
  receipts_with_grand_total_label?: number | null;
  receipts_with_date_or_time_label?: number | null;
  line_count?: number | null;
  word_count?: number | null;
  labeled_word_count?: number | null;
  top_labels?: Record<string, number>;
  blockers?: string[];
  limitations?: string[];
}

export interface TrainingSynthesisSourceQualitySummary {
  merchant_count?: number | null;
  usable_merchant_count?: number | null;
  limited_merchant_count?: number | null;
  blocked_merchant_count?: number | null;
  status_counts?: Record<string, number>;
  receipt_count?: number | null;
  labeled_word_count?: number | null;
  merchants?: TrainingSynthesisSourceQualityMerchant[];
}

export interface TrainingSynthesisScoreSummary {
  count?: number;
  avg?: number;
  min?: number;
  max?: number;
}

export interface TrainingSynthesisLlmExecutionSummary {
  mode_counts?: Record<string, number>;
  paid_llm_disabled_count?: number | null;
  api_call_allowed_count?: number | null;
  configured_models?: string[];
  latest_model_sources?: string[];
  latest_model_verified_at?: string | null;
}

export interface TrainingSynthesisMixBalance {
  accepted_count?: number | null;
  merchant_count?: number | null;
  operation_count?: number | null;
  top_merchant?: string | null;
  top_merchant_count?: number | null;
  top_merchant_share?: number | null;
  top_operation?: string | null;
  top_operation_count?: number | null;
  top_operation_share?: number | null;
  merchant_entropy?: number | null;
  operation_entropy?: number | null;
  risk_level?: string | null;
  risk_reasons?: string[];
}

export interface TrainingSynthesisRealBaselineSummary {
  count?: number | null;
  within_real_score_range_count?: number | null;
  below_real_score_range_count?: number | null;
  within_real_score_range_share?: number | null;
  candidate_score?: TrainingSynthesisScoreSummary | null;
  baseline_avg?: TrainingSynthesisScoreSummary | null;
  baseline_min?: TrainingSynthesisScoreSummary | null;
  baseline_pair_count?: TrainingSynthesisScoreSummary | null;
  delta_from_avg?: TrainingSynthesisScoreSummary | null;
  delta_from_min?: TrainingSynthesisScoreSummary | null;
}

export interface TrainingSynthesisCandidateQuality {
  score?: number | null;
  high_fidelity?: boolean;
  components?: Record<string, number>;
  structure_gate?: {
    min_structure_similarity?: number | null;
    structure_similarity_passed?: boolean;
    component_thresholds?: Record<string, number>;
    passed_components?: string[];
    failed_components?: Record<
      string,
      {
        value?: number | null;
        threshold?: number | null;
      }
    >;
    missing_components?: string[];
    pass_rate?: number | null;
    passed?: boolean;
  };
}

export interface TrainingSynthesisSelectionEvidence {
  schema_version?: string | null;
  selected_from_candidate_count?: number | null;
  selected_input_index?: number | null;
  ranked_by?: string[];
  selected_score?: {
    candidate_quality?: number | null;
    high_fidelity?: boolean | null;
    structure_similarity?: number | null;
    structure_component_pass_rate?: number | null;
    layout_integrity?: number | null;
    token_budget?: number | null;
    within_real_score_range?: boolean | null;
    delta_from_min?: number | null;
    baseline_pair_count?: number | null;
    token_count?: number | null;
  };
  selection_policy?: string | null;
}

export interface TrainingSynthesisCandidateMixMerchant {
  merchant_name?: string;
  candidate_count?: number | null;
  accepted_count?: number | null;
  rejected_count?: number | null;
  rejection_reasons?: Record<string, number>;
  accepted_operation_counts?: Record<string, number>;
  accepted_category_counts?: Record<string, number>;
  accepted_grounded_candidate_count?: number | null;
  accepted_arithmetic_candidate_count?: number | null;
  accepted_structure_similarity?: TrainingSynthesisScoreSummary | null;
  accepted_structure_components?: Record<string, TrainingSynthesisScoreSummary>;
  accepted_real_baseline_comparison?: TrainingSynthesisRealBaselineSummary | null;
  accepted_candidate_quality?: TrainingSynthesisScoreSummary | null;
  accepted_candidate_quality_components?: Record<string, TrainingSynthesisScoreSummary>;
}

export interface TrainingSynthesisMerchantContract {
  merchant_name?: string;
  status?: string | null;
  score?: number | null;
  source_receipt_count?: number | null;
  supported_operations?: string[];
  ready_operations?: string[];
  accepted_operation_counts?: Record<string, number>;
  accepted_category_counts?: Record<string, number>;
  accepted_field_replacement_counts?: Record<string, number>;
  blockers?: string[];
  limitations?: string[];
  tax_contract?: {
    supported_policy?: string | null;
    taxable_item_count?: number | null;
    tax_rate_observation_count?: number | null;
    stable_tax_rate?: boolean;
    avg_tax_rate_percent?: string | null;
    tax_changing_synthesis_ready?: boolean;
    tax_changing_synthesis_blockers?: string[];
  };
}

export interface TrainingSynthesisQualityExample {
  candidate_id?: string | null;
  receipt_key?: string | null;
  operation?: string | null;
  category?: string | null;
  changed_text?: string | null;
  label?: string | null;
  structure_similarity?: number | null;
  structure_evidence?: TrainingSynthesisStructureEvidence;
  candidate_quality?: TrainingSynthesisCandidateQuality;
  selection_evidence?: TrainingSynthesisSelectionEvidence;
  layout_integrity?: TrainingSynthesisLayoutIntegrityEvidence;
  accuracy_checks?: string[];
  receipt_shape?: {
    line_count?: number | null;
    token_count?: number | null;
    truncated?: boolean;
  };
  preview_lines?: TrainingSynthesisReceiptPreviewLine[];
  total_change?: {
    old_grand_total?: string | null;
    new_grand_total?: string | null;
    tax_delta?: string | null;
  };
  catalog_grounding?: {
    product_observed_count?: number | null;
    product_seen_receipt_count?: number | null;
    product_seen_outside_base_count?: number | null;
    product_seen_outside_base?: string[];
    category?: string | null;
    category_seen_count?: number | null;
    category_heading_seen_count?: number | null;
    category_seen_in_receipts?: string[];
  };
  category_placement?: {
    category?: string | null;
    insert_y?: number | null;
    shifted_lower_lines_by?: number | null;
    selection_reason?: string | null;
    base_receipt_has_category?: boolean;
    category_seen_count?: number | null;
    category_heading_seen_count?: number | null;
    category_alignment?: string | null;
  };
  field_replacement?: {
    old_text?: string | null;
    new_text?: string | null;
    format?: string | null;
  };
}

export interface TrainingSynthesisQualityMerchant {
  merchant_name?: string;
  readiness_status?: string | null;
  readiness_score?: number | null;
  source_receipt_count?: number | null;
  source_quality_status?: string | null;
  source_quality_receipt_count?: number | null;
  source_quality_labeled_word_count?: number | null;
  source_quality_receipts_with_line_item_labels?: number | null;
  source_quality_receipts_with_grand_total_label?: number | null;
  source_quality_receipts_with_date_or_time_label?: number | null;
  source_quality_operation_blockers?: Record<string, string>;
  candidate_count?: number | null;
  accepted_count?: number | null;
  rejected_count?: number | null;
  acceptance_rate?: number | null;
  supported_operations?: string[];
  contract_ready_operations?: string[];
  accepted_operation_counts?: Record<string, number>;
  accepted_category_counts?: Record<string, number>;
  accepted_field_replacement_counts?: Record<string, number>;
  safe_mutable_fields?: string[];
  accepted_structure_similarity?: TrainingSynthesisScoreSummary | null;
  accepted_structure_components?: Record<string, TrainingSynthesisScoreSummary>;
  accepted_real_baseline_comparison?: TrainingSynthesisRealBaselineSummary | null;
  accepted_candidate_quality?: TrainingSynthesisScoreSummary | null;
  accepted_candidate_quality_components?: Record<string, TrainingSynthesisScoreSummary>;
  rejection_reasons?: Record<string, number>;
  blockers?: string[];
  limitations?: string[];
  missing_operations?: string[];
  operation_gap_reasons?: Record<string, string[]>;
  accepted_examples?: TrainingSynthesisQualityExample[];
  rejected_examples?: Array<Record<string, unknown>>;
}

export interface TrainingSynthesisOperationCoverage {
  operation_count?: number | null;
  ready_operation_count?: number | null;
  ready_operation_share?: number | null;
  operations?: Record<
    string,
    {
      ready_merchant_count?: number | null;
      merchant_count?: number | null;
      ready_share?: number | null;
      candidate_count?: number | null;
      ready_merchants?: string[];
      blocked_merchants?: Array<{
        merchant_name?: string | null;
        reasons?: string[];
      }>;
    }
  >;
  recommendations?: string[];
}

export interface TrainingSynthesisAcceptedOperationCoverage {
  operation_count?: number | null;
  ready_operation_count?: number | null;
  accepted_operation_count?: number | null;
  accepted_ready_operation_count?: number | null;
  accepted_ready_operation_share?: number | null;
  uncovered_ready_operations?: string[];
  operations?: Record<
    string,
    {
      ready_merchant_count?: number | null;
      accepted_merchant_count?: number | null;
      accepted_ready_merchant_count?: number | null;
      accepted_count?: number | null;
      ready_acceptance_share?: number | null;
      ready_merchants?: string[];
      accepted_merchants?: string[];
      uncovered_ready_merchants?: string[];
    }
  >;
  recommendations?: string[];
}

export interface TrainingSynthesisMerchantGapSummary {
  blocked_merchant_count?: number | null;
  merchant_gap_count?: number | null;
  top_blockers?: Record<string, number>;
  top_limitations?: Record<string, number>;
  merchants?: Array<{
    merchant_name?: string | null;
    status?: string | null;
    score?: number | null;
    candidate_count?: number | null;
    accepted_count?: number | null;
    ready_operation_count?: number | null;
    missing_operations?: string[];
    operation_gap_reasons?: Record<string, string[]>;
    blockers?: string[];
    limitations?: string[];
  }>;
}

export interface TrainingSynthesisQualityReport {
  ready?: boolean;
  training_ready?: boolean;
  training_ready_reasons?: string[];
  bundle_ready?: boolean;
  bundle_reasons?: string[];
  summary?: {
    merchant_count?: number | null;
    accepted_merchant_count?: number | null;
    candidate_count?: number | null;
    accepted_count?: number | null;
    rejected_count?: number | null;
    acceptance_rate?: number | null;
    accepted_operation_counts?: Record<string, number>;
    accepted_category_counts?: Record<string, number>;
    accepted_field_replacement_counts?: Record<string, number>;
    accepted_structure_similarity?: TrainingSynthesisScoreSummary | null;
    accepted_structure_components?: Record<string, TrainingSynthesisScoreSummary>;
    accepted_real_baseline_comparison?: TrainingSynthesisRealBaselineSummary | null;
    accepted_candidate_quality?: TrainingSynthesisScoreSummary | null;
    accepted_candidate_quality_components?: Record<string, TrainingSynthesisScoreSummary>;
    accepted_mix_balance?: TrainingSynthesisMixBalance;
    llm_execution?: TrainingSynthesisLlmExecutionSummary;
    rejection_reasons?: Record<string, number>;
    contract_count?: number | null;
    ready_contract_count?: number | null;
    source_quality_status_counts?: Record<string, number>;
    blocked_source_quality_merchant_count?: number | null;
  };
  operation_coverage?: TrainingSynthesisOperationCoverage;
  accepted_operation_coverage?: TrainingSynthesisAcceptedOperationCoverage;
  merchant_gap_summary?: TrainingSynthesisMerchantGapSummary;
  quality_gates?: {
    validation_policy?: string | null;
    train_only_examples?: boolean;
    contract_gate?: Record<string, unknown>;
    max_per_merchant?: number | null;
    max_per_merchant_operation?: number | null;
    min_structure_similarity?: number | null;
    structure_component_thresholds?: Record<string, number>;
    accepted_operation_coverage_gate?: {
      enabled?: boolean;
      passed?: boolean;
      ready_operation_count?: number | null;
      accepted_ready_operation_count?: number | null;
      uncovered_ready_operations?: string[];
    };
    llm_model_freshness_gate?: {
      enabled?: boolean;
      passed?: boolean;
      requires_current_model_guidance?: boolean;
      api_call_allowed_count?: number | null;
      llm_assisted_mode_count?: number | null;
      latest_model_verified_at?: string | null;
      latest_model_age_days?: number | null;
      max_age_days?: number | null;
      latest_model_sources?: string[];
      reason?: string | null;
    };
  };
  recommendations?: string[];
  merchants?: TrainingSynthesisQualityMerchant[];
}

export interface TrainingSynthesisSummary {
  status: "available" | "metrics_only" | "artifact_unavailable";
  artifact_s3_uri?: string;
  artifact_schema_version?: string;
  synthetic_train_examples?: number | null;
  synthetic_candidates_seen?: number;
  synthetic_candidates_accepted?: number;
  synthetic_candidates_rejected?: number;
  synthetic_rejection_reasons?: Record<string, number>;
  rejected_count?: number | null;
  rejection_reasons?: Record<string, number>;
  bundle_candidates_seen?: number;
  bundle_candidates_accepted?: number;
  bundle_candidates_rejected?: number;
  bundle_rejection_reasons?: Record<string, number>;
  bundle_max_per_merchant?: number;
  bundle_max_per_merchant_operation?: number;
  bundle_min_structure_similarity?: number;
  candidate_count?: number;
  recipe_count?: number;
  merchant_count?: number;
  accepted_merchant_count?: number | null;
  merchants?: string[];
  source_counts?: Record<string, number>;
  operation_counts?: Record<string, number>;
  accepted_operation_counts?: Record<string, number>;
  field_replacement_counts?: Record<string, number>;
  accepted_field_replacement_counts?: Record<string, number>;
  category_counts?: Record<string, number>;
  accepted_category_counts?: Record<string, number>;
  grounded_candidate_count?: number;
  grounded_candidate_share?: number | null;
  accepted_grounded_candidate_count?: number | null;
  best_structure_similarity?: number | null;
  avg_structure_similarity?: number | null;
  accepted_structure_similarity?: TrainingSynthesisScoreSummary | null;
  accepted_structure_components?: Record<string, TrainingSynthesisScoreSummary>;
  accepted_real_baseline_comparison?: TrainingSynthesisRealBaselineSummary | null;
  synthetic_accepted_real_baseline_comparison?: TrainingSynthesisRealBaselineSummary | null;
  accepted_candidate_quality?: TrainingSynthesisScoreSummary | null;
  accepted_candidate_quality_components?: Record<string, TrainingSynthesisScoreSummary>;
  accepted_mix_balance?: TrainingSynthesisMixBalance;
  synthetic_accepted_mix_balance?: TrainingSynthesisMixBalance;
  avg_structure_components?: Record<string, number>;
  arithmetic_candidate_count?: number;
  accepted_arithmetic_candidate_count?: number | null;
  non_taxable_arithmetic_candidate_count?: number;
  arithmetic_update_counts?: Record<string, number>;
  profile_receipt_count?: number;
  catalog_item_count?: number;
  category_count?: number;
  readiness_status_counts?: Record<string, number>;
  ready_merchant_count?: number;
  avg_readiness_score?: number | null;
  merchant_readiness?: TrainingSynthesisMerchantReadiness[];
  contract_merchant_count?: number | null;
  contract_ready_merchant_count?: number | null;
  contract_operation_counts?: Record<string, number>;
  contract_field_replacement_counts?: Record<string, number>;
  accepted_operation_coverage?: TrainingSynthesisAcceptedOperationCoverage;
  synthetic_accepted_operation_coverage?: TrainingSynthesisAcceptedOperationCoverage;
  merchant_synthesis_contracts?: TrainingSynthesisMerchantContract[];
  candidate_mix_merchants?: TrainingSynthesisCandidateMixMerchant[];
  source_receipt_quality?: TrainingSynthesisSourceQualitySummary;
  quality_report?: TrainingSynthesisQualityReport;
  llm_execution?: TrainingSynthesisLlmExecutionSummary;
  top_catalog_items?: TrainingSynthesisCatalogItem[];
  candidate_examples?: TrainingSynthesisCandidateExample[];
  validation_policy?: string;
}

export interface TrainingMetricsResponse {
  job_id: string;
  job_name: string;
  status: string;
  created_at: string;
  dataset_metrics?: DatasetMetrics;
  synthesis?: TrainingSynthesisSummary | null;
  epochs: TrainingMetricsEpoch[];
  best_epoch: number;
  best_f1: number;
  total_epochs: number;
}

// LayoutLM Batch Inference Types

export interface LayoutLMPrediction {
  word_id: number | null;
  line_id: number;
  text: string;
  predicted_label: string;
  predicted_label_base: string;
  ground_truth_label: string | null;
  ground_truth_label_base: string | null;
  ground_truth_label_original?: string | null;
  predicted_confidence: number;
  is_correct: boolean;
  all_class_probabilities?: Record<string, number>;
  all_class_probabilities_base?: Record<string, number>;
}

export interface LayoutLMReceiptWord {
  receipt_id: number;
  line_id: number;
  word_id: number;
  text: string;
  bounding_box: BoundingBox;
  top_left?: Point;
  top_right?: Point;
  bottom_left?: Point;
  bottom_right?: Point;
}

export interface LayoutLMEntitiesSummary {
  merchant_name: string | null;
  date: string | null;
  address: string | null;
  address_line?: string | null;
  phone_number?: string | null;
  amount: string | null;
}

export interface LayoutLMReceiptInference {
  receipt_id: string;
  original: {
    receipt: {
      image_id: string;
      receipt_id: number;
      width: number;
      height: number;
      cdn_s3_bucket: string;
      cdn_s3_key: string;
      cdn_webp_s3_key?: string;
      cdn_avif_s3_key?: string;
      cdn_medium_s3_key?: string;
      cdn_medium_webp_s3_key?: string;
      cdn_medium_avif_s3_key?: string;
    };
    words: LayoutLMReceiptWord[];
    predictions: LayoutLMPrediction[];
  };
  // Present on the legacy generated cache; absent on records sourced from the
  // per-epoch eval (the viz only reads `original` + `inference_time_ms`).
  metrics?: {
    overall_accuracy: number;
    total_words: number;
    correct_predictions: number;
    per_label_f1?: Record<string, number>;
    per_label_precision?: Record<string, number>;
    per_label_recall?: Record<string, number>;
  };
  model_info?: {
    model_name: string;
    device: string;
    s3_uri: string;
  };
  entities_summary?: LayoutLMEntitiesSummary;
  inference_time_ms: number;
  cached_at?: string;
}

export interface LayoutLMAggregateStats {
  avg_accuracy: number;
  min_accuracy: number;
  max_accuracy: number;
  avg_inference_time_ms: number;
  total_receipts_in_pool: number;
  batch_size: number;
  total_words_processed: number;
  estimated_throughput_per_hour: number;
}

export interface LayoutLMBatchInferenceResponse {
  receipts: LayoutLMReceiptInference[];
  aggregate_stats: LayoutLMAggregateStats;
  fetched_at: string;
  legacy_mode?: boolean;
}

// Per-Epoch Checkpoint Evaluation Types
// Produced by the eval-checkpoints SageMaker Processing job and served by
// GET /layoutlm_epochs. Each entry re-scores one checkpoint on the run's
// frozen validation set, so the curve proves which epoch generalizes best.

export interface EpochEvaluationEntry {
  checkpoint: string;
  step: number | null;
  epoch: number | null;
  heldout_f1: number;
  heldout_precision: number;
  heldout_recall: number;
  heldout_metric: string;
  per_label_f1: Record<string, number>;
  token_accuracy: number;
  // The value training reported for this epoch from inside the loop (for
  // comparison against the honest held-out re-evaluation). May be null.
  training_reported_f1: number | null;
  num_receipts_evaluated: number;
  // Windowed-inference wall-time for this checkpoint over the val set. Avg is
  // per-receipt; null on older caches generated before timing was recorded.
  avg_inference_ms: number | null;
  total_inference_ms: number | null;
}

// Compute the eval ran on, so timing can be labeled GPU vs CPU.
export interface EpochEvaluationCompute {
  device: string; // "cuda" | "cpu" | "unknown"
  gpu_name: string | null;
  instance_type: string | null;
}

export interface EpochEvaluationResponse {
  job_name: string;
  run_s3_uri: string;
  // "persisted_val_receipt_keys" (drift-proof) or "reconstructed_from_seed".
  val_set_source: string;
  val_receipts_hash: string;
  val_receipts_hash_recorded: string | null;
  val_receipts_hash_verified: boolean;
  num_val_receipts: number;
  random_seed: number | null;
  label_list: string[] | null;
  label_merges: Record<string, string[]>;
  metric: string;
  // Present on caches generated after timing was added; absent on older ones.
  compute?: EpochEvaluationCompute;
  epochs: EpochEvaluationEntry[];
  best_epoch_heldout: number | null;
  best_checkpoint_heldout: string | null;
  best_epoch_training_reported: number | null;
  showcase_receipt_keys: string[];
  generated_at: string;
}

export interface EpochEvaluationJobsResponse {
  jobs: string[];
}

// Per-(epoch, receipt) showcase record for the epoch scrubber. The `original`
// block matches LayoutLMReceiptInference so the same receipt+bbox rendering
// applies.
export interface EpochEvaluationReceiptRecord {
  receipt_id: string;
  epoch: number | null;
  checkpoint: string;
  label_list: string[];
  original: {
    receipt: LayoutLMReceiptInference["original"]["receipt"];
    words: LayoutLMReceiptWord[];
    predictions: LayoutLMPrediction[];
  };
  inference_time_ms: number;
}

// Label Evaluator Visualization Types

export interface LabelEvaluatorWord {
  text: string;
  label: string | null;
  line_id: number;
  word_id: number;
  bbox: {
    x: number;
    y: number;
    width: number;
    height: number;
  };
}

export interface LabelEvaluatorIssue {
  type: string;
  word_text: string;
  word_id: number;
  line_id: number;
  current_label: string | null;
  suggested_label: string;
  suggested_status: string;
  reasoning: string;
}

export interface LabelEvaluatorDecision {
  image_id: string;
  receipt_id: number;
  issue: {
    line_id: number;
    word_id: number;
    current_label: string;
    word_text: string;
  };
  llm_review: {
    decision: "VALID" | "INVALID" | "NEEDS_REVIEW";
    reasoning: string;
    suggested_label: string | null;
    confidence: "high" | "medium" | "low";
  };
}

export interface ReviewEvidence {
  word_text: string;
  similarity_score: number;
  label_valid: boolean;
  evidence_source: "words" | "lines";
  is_same_merchant: boolean;
}

export interface ReviewDecision {
  image_id: string;
  receipt_id: number;
  consensus_score: number;
  similar_word_count: number;
  issue: {
    type: string;
    line_id: number;
    word_id: number;
    word_text: string;
    current_label: string | null;
    suggested_label: string;
    suggested_status: string;
    reasoning: string;
  };
  evidence: ReviewEvidence[];
  llm_review: {
    decision: "VALID" | "INVALID" | "NEEDS_REVIEW";
    reasoning: string;
    suggested_label: string | null;
    confidence: "high" | "medium" | "low";
  };
}

export interface LabelEvaluatorEvaluation {
  image_id: string;
  receipt_id: number;
  merchant_name: string;
  duration_seconds: number;
  decisions: {
    VALID: number;
    INVALID: number;
    NEEDS_REVIEW: number;
  };
  all_decisions: (LabelEvaluatorDecision | ReviewDecision)[];
}

export interface LabelEvaluatorGeometric {
  image_id: string;
  receipt_id: number;
  issues_found: number;
  issues: LabelEvaluatorIssue[];
  error: string | null;
  merchant_receipts_analyzed: number;
  label_types_found: number;
  duration_seconds?: number;
}

export interface LabelEvaluatorReceipt {
  image_id: string;
  receipt_id: number;
  merchant_name: string | null;
  issues_found: number;
  words: LabelEvaluatorWord[];
  geometric: LabelEvaluatorGeometric;
  currency: LabelEvaluatorEvaluation;
  metadata: LabelEvaluatorEvaluation;
  financial: LabelEvaluatorEvaluation;
  review?: LabelEvaluatorEvaluation;
  line_item_duration_seconds?: number | null;
  cdn_s3_key: string;
  cdn_webp_s3_key?: string;
  cdn_avif_s3_key?: string;
  cdn_medium_s3_key?: string;
  cdn_medium_webp_s3_key?: string;
  cdn_medium_avif_s3_key?: string;
  width: number;
  height: number;
}

export interface LabelEvaluatorAggregateStats {
  total_receipts_in_pool: number;
  batch_size: number;
  avg_issues: number;
  max_issues: number;
  receipts_with_issues: number;
}

export interface LabelEvaluatorResponse {
  receipts: LabelEvaluatorReceipt[];
  total_count: number;
  offset: number;
  has_more: boolean;
  seed: number;
  aggregate_stats: LabelEvaluatorAggregateStats;
  execution_id?: string;
  cached_at?: string;
  fetched_at?: string;
}

// ============================================================================
// Label Validation Visualization Types (receipt-label-validation project)
// ============================================================================

/**
 * Individual word validation result from the label validation pipeline.
 * Each word is validated by ChromaDB consensus (Tier 1) or LLM (Tier 2).
 */
export interface LabelValidationWord {
  text: string;
  line_id: number;
  word_id: number;
  bbox: {
    x: number;
    y: number;
    width: number;
    height: number;
  };
  label: string;
  validation_status?: "NONE" | "PENDING" | "VALID" | "INVALID" | "NEEDS_REVIEW";
  validation_source: "chroma" | "llm" | null;
  decision: "VALID" | "INVALID" | "CORRECTED" | "NEEDS_REVIEW" | null;
}

/**
 * Validation tier results (ChromaDB or LLM) for the two-tier validation system.
 */
export interface LabelValidationTier {
  tier: "chroma" | "llm";
  duration_seconds: number;
  words_count: number;
  decisions: {
    VALID: number;
    INVALID: number;
    NEEDS_REVIEW: number;
    UNKNOWN?: number;
  };
}

/**
 * Receipt with label validation results.
 * Contains words with their validation decisions and CDN image keys.
 */
export interface LabelValidationReceipt {
  image_id: string;
  receipt_id: number;
  merchant_name: string | null;
  words: LabelValidationWord[];
  chroma: LabelValidationTier;
  llm: LabelValidationTier | null;
  step_timings?: Record<
    string,
    { duration_ms: number; duration_seconds: number }
  >;
  cdn_s3_key: string;
  cdn_webp_s3_key?: string;
  cdn_avif_s3_key?: string;
  cdn_medium_s3_key?: string;
  cdn_medium_webp_s3_key?: string;
  cdn_medium_avif_s3_key?: string;
  width: number;
  height: number;
}

/**
 * Aggregate statistics for the label validation visualization.
 */
export interface LabelValidationAggregateStats {
  avg_chroma_rate: number;
  avg_confidence: number;
  total_receipts: number;
  total_valid: number;
  total_invalid: number;
  total_needs_review: number;
}

/**
 * API response for the label validation visualization endpoint.
 */
export interface LabelValidationResponse {
  receipts: LabelValidationReceipt[];
  total_count: number;
  offset: number;
  has_more: boolean;
  seed: number;
  aggregate_stats: LabelValidationAggregateStats;
  cached_at?: string;
  fetched_at?: string;
}

// Financial Math Overlay Types

export interface FinancialMathWord {
  line_id: number;
  word_id: number;
  word_text: string;
  current_label: string;
  bbox: { x: number; y: number; width: number; height: number };
  decision: string;
  confidence: string;
  reasoning: string;
}

export interface FinancialMathEquation {
  issue_type: string;
  description: string;
  expected_value: number | string | null;
  actual_value: number | string | null;
  difference: number | string | null;
  involved_words: FinancialMathWord[];
}

export interface FinancialMathReceipt {
  image_id: string;
  receipt_id: number;
  merchant_name: string | null;
  trace_id: string;
  receipt_type?: "itemized" | "service" | "terminal";
  equations: FinancialMathEquation[];
  summary: {
    total_equations: number;
    total_confirmed?: number;
    has_invalid: boolean;
    has_needs_review: boolean;
  };
  // CDN image keys (populated by Spark cache enrichment)
  cdn_s3_key: string;
  cdn_webp_s3_key?: string;
  cdn_avif_s3_key?: string;
  cdn_medium_s3_key?: string;
  cdn_medium_webp_s3_key?: string;
  cdn_medium_avif_s3_key?: string;
  width: number;
  height: number;
  reocr_region?: {
    x: number;
    y: number;
    width: number;
    height: number;
  };
}

export interface FinancialMathResponse {
  receipts: FinancialMathReceipt[];
  total_count: number;
  offset: number;
  has_more: boolean;
  seed: number;
}

// ============================================================================
// Within-Receipt Verification Visualization Types
// ============================================================================

export interface WithinReceiptPlaceInfo {
  merchant_name: string | null;
  formatted_address: string | null;
  phone_number: string | null;
  website: string | null;
  maps_url: string | null;
  validation_status: string | null;
  confidence: number | null;
  business_status: string | null;
  latitude: number | null;
  longitude: number | null;
}

export interface WithinReceiptWordDecision {
  line_id: number;
  word_id: number;
  word_text: string;
  current_label: string;
  decision: "VALID" | "INVALID" | "NEEDS_REVIEW" | null;
  confidence: "high" | "medium" | "low" | null;
  reasoning: string | null;
  bbox: {
    x: number;
    y: number;
    width: number;
    height: number;
  };
}

export interface WithinReceiptEquation {
  issue_type: string;
  description: string;
  expected_value: number | null;
  actual_value: number | null;
  difference: number | null;
  involved_words: WithinReceiptWordDecision[];
}

export interface WithinReceiptPlaceValidation {
  place: WithinReceiptPlaceInfo | null;
  decisions: WithinReceiptWordDecision[];
  summary: {
    total: number;
    valid: number;
    invalid: number;
    needs_review: number;
  };
  duration_seconds: number | null;
  is_llm: boolean;
}

export interface WithinReceiptFormatValidation {
  decisions: WithinReceiptWordDecision[];
  summary: {
    total: number;
    valid: number;
    invalid: number;
    needs_review: number;
  };
  duration_seconds: number | null;
  is_llm: boolean;
}

export interface WithinReceiptFinancialMath {
  equations: WithinReceiptEquation[];
  summary: {
    total_equations: number;
    has_invalid: boolean;
    has_needs_review: boolean;
  };
  duration_seconds: number | null;
  is_llm: boolean;
}

export interface WithinReceiptWord {
  text: string;
  label: string | null;
  line_id: number;
  word_id: number;
  bbox: {
    x: number;
    y: number;
    width: number;
    height: number;
  };
}

export interface WithinReceiptVerificationReceipt {
  image_id: string;
  receipt_id: number;
  merchant_name: string | null;
  trace_id: string | null;
  place_validation: WithinReceiptPlaceValidation;
  format_validation: WithinReceiptFormatValidation;
  financial_math: WithinReceiptFinancialMath;
  words: WithinReceiptWord[];
  cdn_s3_key: string;
  cdn_webp_s3_key?: string;
  cdn_avif_s3_key?: string;
  cdn_medium_s3_key?: string;
  cdn_medium_webp_s3_key?: string;
  cdn_medium_avif_s3_key?: string;
  width: number;
  height: number;
}

export interface WithinReceiptVerificationResponse {
  receipts: WithinReceiptVerificationReceipt[];
  total_count: number;
  offset: number;
  has_more: boolean;
  seed: number;
  aggregate_stats: LabelEvaluatorAggregateStats;
  execution_id?: string;
  cached_at?: string;
  fetched_at?: string;
}

// ============================================================================
// Receipt Health Visualization Types
// ============================================================================

export type ReceiptHealthStatus =
  | "pass"
  | "review"
  | "fail"
  | "not_applicable";

export interface ReceiptHealthSummary {
  total_checks: number;
  passed: number;
  needs_review: number;
  failed: number;
  not_applicable: number;
  issue_count: number;
}

export interface ReceiptHealthCheck {
  id: "merchant_identity" | "receipt_format" | "financial_math";
  title: string;
  question: string;
  status: ReceiptHealthStatus;
  validator: "place_validation" | "format_validation" | "financial_math";
  is_llm: boolean;
  duration_seconds: number | null;
  summary:
    | {
        total: number;
        valid: number;
        invalid: number;
        needs_review: number;
      }
    | {
        total_equations: number;
        has_invalid: boolean;
        has_needs_review: boolean;
        mismatched_equations?: number;
      };
  result: string;
  evidence_count: number;
  what_it_validates: string[];
}

export interface ReceiptHealthPrimaryIssue {
  check_id: ReceiptHealthCheck["id"];
  title: string;
  status: ReceiptHealthStatus;
  message: string;
  summary?: string;
  issue_count: number;
}

export interface ReceiptHealthAggregateStats {
  total_receipts_in_pool: number;
  batch_size: number;
  passed: number;
  needs_review: number;
  failed: number;
  not_applicable: number;
  receipts_with_issues: number;
  total_issues: number;
}

export interface ReceiptHealthReceipt extends WithinReceiptVerificationReceipt {
  receipt_type?: "itemized" | "service" | "terminal";
  overall_status: ReceiptHealthStatus;
  summary: ReceiptHealthSummary;
  checks: ReceiptHealthCheck[];
  primary_issues: ReceiptHealthPrimaryIssue[];
  cdn_thumbnail_s3_key?: string;
  cdn_thumbnail_webp_s3_key?: string;
  cdn_thumbnail_avif_s3_key?: string;
  cdn_small_s3_key?: string;
  cdn_small_webp_s3_key?: string;
  cdn_small_avif_s3_key?: string;
}

export interface ReceiptHealthResponse {
  receipts: ReceiptHealthReceipt[];
  total_count: number;
  offset: number;
  has_more: boolean;
  seed: number;
  aggregate_stats: ReceiptHealthAggregateStats;
  execution_id?: string;
  cached_at?: string;
  fetched_at?: string;
}

export type ReceiptHealthIssueState =
  | "open"
  | "claimed"
  | "awaiting_validation"
  | "resolved"
  | "blocked"
  | "manual_review"
  | "known_limitation";

export type ReceiptHealthPreflightClassification =
  | "needs_ai_review"
  | "safe_exact_plan"
  | "known_limitation"
  | "reocr_needed"
  | "evaluator_rule_gap";

export interface ReceiptHealthPreflightAction {
  tool: "update_word_label" | "create_word_label" | "trigger_reocr";
  image_id?: string;
  receipt_id?: number;
  line_id?: number;
  word_id?: number;
  label?: string;
  new_status?: "VALID" | "INVALID" | "NEEDS_REVIEW";
  reasoning?: string;
}

export interface ReceiptHealthSectionEvidenceRow {
  row_index: number;
  line_ids: number[];
  section: string;
  text: string;
}

export interface ReceiptHealthSectionEvidence {
  issue_sections?: Record<string, number>;
  context_sections?: Record<string, number>;
  issue_rows?: ReceiptHealthSectionEvidenceRow[];
  has_tip_suggestions?: boolean;
  has_tip_entry_area?: boolean;
  has_void_discount?: boolean;
  has_payment_summary?: boolean;
}

export interface ReceiptHealthLineItemAmountEvidenceRow {
  row_index: number;
  line_ids: number[];
  text: string;
  clean_amount_tokens?: string[];
  fragment_amount_tokens?: string[];
  has_labeled_line_total_candidate?: boolean;
}

export interface ReceiptHealthLineItemAmountEvidence {
  item_amount_row_count?: number;
  clean_amount_row_count?: number;
  fragmented_amount_row_count?: number;
  labeled_line_total_row_count?: number;
  rows?: ReceiptHealthLineItemAmountEvidenceRow[];
}

export interface ReceiptHealthPreflightEvidence {
  section_evidence?: ReceiptHealthSectionEvidence;
  line_item_amount_evidence?: ReceiptHealthLineItemAmountEvidence;
  [key: string]: unknown;
}

export interface ReceiptHealthIssuePreflight {
  version: string;
  classification: ReceiptHealthPreflightClassification | string;
  automation_lane: "deterministic" | "ai_review" | "none" | string;
  lane: string;
  root_cause: string;
  recommended_next_step?: string;
  is_automation_ready: boolean;
  summary: string;
  proposed_actions: ReceiptHealthPreflightAction[];
  action_count: number;
  evidence?: ReceiptHealthPreflightEvidence;
  data_fingerprint?: string;
}

export interface ReceiptHealthLedgerIssue {
  issue_id: string;
  fingerprint: string;
  execution_id: string;
  observed_at: string;
  image_id: string;
  receipt_id: number;
  merchant_name?: string;
  receipt_type?: "itemized" | "service" | "terminal";
  check_id: ReceiptHealthCheck["id"];
  check_title: string;
  validator: ReceiptHealthCheck["validator"];
  status: ReceiptHealthStatus;
  issue_type: string;
  message: string;
  result?: string;
  evidence: Array<Record<string, unknown>>;
  state?: ReceiptHealthIssueState;
  preflight?: ReceiptHealthIssuePreflight;
  first_seen_at?: string;
  first_seen_execution_id?: string;
  last_seen_at?: string;
  last_seen_execution_id?: string;
  occurrence_count?: number;
  attempt_count?: number;
  last_attempted_at?: string;
  last_attempt_summary?: string;
  known_limitation_reason?: string;
  suppression_fingerprint?: string;
}

export interface ReceiptHealthLedgerSummary {
  total_issues: number;
  by_state: Record<string, number>;
  by_check: Record<string, number>;
  by_preflight_classification?: Record<string, number>;
  by_preflight_lane?: Record<string, number>;
  by_preflight_root_cause?: Record<string, number>;
  eligible_issues: number;
}

export interface ReceiptHealthIssuesResponse {
  issues: ReceiptHealthLedgerIssue[];
  count?: number;
  limit?: number;
  state?: string;
  summary?: ReceiptHealthLedgerSummary;
  latest_execution_id?: string;
  execution_id?: string;
  updated_at?: string;
  cached_at?: string;
  fetched_at?: string;
}
