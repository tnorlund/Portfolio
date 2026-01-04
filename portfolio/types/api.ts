export interface LabelValidationCountResponse {
  [key: string]: {
    [key: string]: number;
  };
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

export interface TrainingMetricsResponse {
  job_id: string;
  job_name: string;
  status: string;
  created_at: string;
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

// Geometric Anomaly Cache Types

export interface GeometricAnomalyWord {
  word_id: number;
  line_id: number;
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
  label: string | null;
  is_flagged: boolean;
  anomaly_type?: string;
  reasoning?: string;
}

export interface GeometricLabelPair {
  from_label: string;
  to_label: string;
  observations: Array<{ dx: number; dy: number }>;
  mean: { dx: number; dy: number };
  std_deviation: number;
}

export interface GeometricFlaggedWord {
  word_id: number;
  line_id: number;
  current_label: string | null;
  reference_label: string | null;
  expected: { dx: number; dy: number };
  actual: { dx: number; dy: number };
  z_score: number;
  threshold: number;
  reasoning?: string;
}

// Constellation Types

export interface ConstellationMember {
  label: string;
  expected: { dx: number; dy: number };
  actual?: { dx: number; dy: number };
  is_flagged?: boolean;
  std_deviation: number;
}

export interface ConstellationInfo {
  labels: string[];
  observation_count: number;
  members: ConstellationMember[];
}

export interface ConstellationAnomaly {
  word_id: number;
  line_id: number;
  labels: string[];
  flagged_label: string;
  expected: { dx: number; dy: number };
  actual: { dx: number; dy: number };
  deviation: number;
  threshold: number;
  reasoning: string;
}

export interface ReceiptImageData {
  width: number;
  height: number;
  cdn_s3_key: string | null;
  cdn_webp_s3_key?: string | null;
  cdn_avif_s3_key?: string | null;
  cdn_medium_s3_key?: string | null;
  cdn_medium_webp_s3_key?: string | null;
  cdn_medium_avif_s3_key?: string | null;
}

export interface GeometricAnomalyCacheResponse {
  receipt: {
    image_id: string;
    receipt_id: number;
    merchant_name: string;
    words: GeometricAnomalyWord[];
    image?: ReceiptImageData | null;
  };
  patterns: {
    label_pairs: GeometricLabelPair[];
  };
  flagged_word?: GeometricFlaggedWord;
  constellation?: ConstellationInfo;
  constellation_anomaly?: ConstellationAnomaly;
  cached_at: string;
  fetched_at?: string;
  pool_size?: number;
  execution_id?: string;
}

// LLM Evaluator Cache Types

export type LLMDecision = "VALID" | "INVALID" | "NEEDS_REVIEW";
export type LLMConfidence = "high" | "medium" | "low";

export interface LLMEvaluation {
  word_text: string;
  current_label: string;
  decision: LLMDecision;
  reasoning: string;
  suggested_label?: string;
  confidence: LLMConfidence;
}

export interface LLMFinancialResult {
  equation: string;
  subtotal: number;
  tax: number;
  expected_total: number;
  actual_total: number;
  difference: number;
  decision: LLMDecision;
  reasoning: string;
  wrong_value: "SUBTOTAL" | "TAX" | "GRAND_TOTAL" | null;
}

export interface LLMPipelineStage {
  id: string;
  name: string;
  status: "pending" | "processing" | "complete";
}

export interface LLMEvaluatorCacheResponse {
  receipt: {
    image_id: string;
    receipt_id: number;
    merchant_name: string;
    subtotal: number;
    tax: number;
    grand_total: number;
    line_items: Array<{ name: string; price: number }>;
  };
  evaluations: {
    currency: LLMEvaluation[];
    metadata: LLMEvaluation[];
    financial: LLMFinancialResult;
  };
  pipeline: LLMPipelineStage[];
  cached_at: string;
  fetched_at?: string;
  pool_size?: number;
  execution_id?: string;
}
