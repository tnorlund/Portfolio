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
