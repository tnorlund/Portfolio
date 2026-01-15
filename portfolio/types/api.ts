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

export interface ImageCountApiResponse {
  count: number;
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
  lines: Line[];
  bbox: AddressBoundingBox | null;
}

export interface MilkSimilarityResponse {
  query_word: string;
  total_receipts: number;
  total_items: number;
  summary_table: MilkSummaryRow[];
  receipts: MilkReceiptData[];
  cached_at: string;
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
}

export interface TrainingMetricsResponse {
  job_id: string;
  job_name: string;
  status: string;
  created_at: string;
  dataset_metrics?: DatasetMetrics;
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
  metrics: {
    overall_accuracy: number;
    total_words: number;
    correct_predictions: number;
    per_label_f1?: Record<string, number>;
    per_label_precision?: Record<string, number>;
    per_label_recall?: Record<string, number>;
  };
  model_info: {
    model_name: string;
    device: string;
    s3_uri: string;
  };
  entities_summary: LayoutLMEntitiesSummary;
  inference_time_ms: number;
  cached_at: string;
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
  all_decisions: LabelEvaluatorDecision[];
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
  merchant_name?: string;
  issues_found: number;
  words: LabelEvaluatorWord[];
  geometric: LabelEvaluatorGeometric;
  currency: LabelEvaluatorEvaluation;
  metadata: LabelEvaluatorEvaluation;
  financial: LabelEvaluatorEvaluation;
  // Review runs after Geometric if issues were found - produces V/I/R decisions
  review?: LabelEvaluatorEvaluation;
  // Line item structure discovery duration (seconds)
  line_item_duration_seconds?: number | null;
  // CDN image keys
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
