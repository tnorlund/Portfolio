// import Receipt from "./Receipt";

// Shared interfaces
export interface Point {
  x: number;
  y: number;
}

export interface BoundingBoxInterface {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface LineItem {
  image_id: number;
  id: number;
  text: string;
  bounding_box: BoundingBoxInterface;
  top_left: Point;
  top_right: Point;
  bottom_left: Point;
  bottom_right: Point;
  angle_degrees: number;
  angle_radians: number;
  confidence: number;
}

export interface Image {
  image_id: string; // (str): UUID identifying the image.
  width: number; // (int): The width of the image in pixels.
  height: number; // (int): The height of the image in pixels.
  timestamp_added: string; //  (datetime): The timestamp when the image was added.
  raw_s3_bucket: string; // (str): The S3 bucket where the image is initially stored.
  raw_s3_key: string; // (str): The S3 key where the image is initially stored.
  sha256: string; // (str): The SHA256 hash of the image.
  cdn_s3_bucket: string; //  (str): The S3 bucket where the image is stored in the CDN.
  cdn_s3_key: string; // (str): The S3 key where the image is stored in the CDN.
}

export interface Word {
  // IDs indicating what this word is linked to
  image_id: number;
  line_id: number;
  word_id: number;

  // Basic text and geometry
  text: string;
  bounding_box: BoundingBoxInterface;
  top_left: Point;
  top_right: Point;
  bottom_left: Point;
  bottom_right: Point;
  angle_degrees: number;
  angle_radians: number;
  confidence: number;

  // Additional fields found in the example
  tags: string[];
  histogram: Record<string, number>;
  num_chars: number;
}

export interface ReceiptWord {
  image_id: string;  // UUID string
  receipt_id: number;
  line_id: number;
  word_id: number;
  text: string;
  bounding_box: BoundingBoxInterface;
  top_left: Point;
  top_right: Point;
  bottom_left: Point;
  bottom_right: Point;
  angle_degrees: number;
  angle_radians: number;
  confidence: number;
  tags: string[];  // Required array, might be empty
  histogram: Record<string, number>;  // Required
  num_chars: number;  // Required
}

export interface ReceiptWordTag {
  image_id: string;
  receipt_id: number;
  line_id: number;
  word_id: number;
  tag: string;
  timestamp_added: string;
  validated: boolean | null;
  timestamp_validated: string | null;
  gpt_confidence: number | null;
  flag: string | null;
  revised_tag: string | null;
  human_validated: boolean | null;
  timestamp_human_validated: string | null;
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
}

export interface ImagePayload {
  id: number;
  width: number;
  height: number;
  timestamp_added: string;
  raw_s3_bucket: string;
  raw_s3_key: string;
  sha256: string;
  cdn_s3_bucket: string;
  cdn_s3_key: string;
}

export interface PayloadItem {
  image: ImagePayload;
  lines: LineItem[];
  receipts: Receipt[];
}

export interface RootPayload {
  [key: string]: PayloadItem;
}

export interface LastEvaluatedKey {}

export interface ReceiptPayloadEntry {
  receipt?: Receipt; // The single "receipt" object
  words?: ReceiptWord[]; // The array of recognized word objects
}
export interface ReceiptPayload_new {
  [key: string]: ReceiptPayloadEntry;
}

export interface ReceiptDetailApiResponse {
  receipt: Receipt;
  words: ReceiptWord[];
  tags: ReceiptWordTag[];
}

export interface ReceiptDetailsApiResponse {
  payload: {
    [key: string]: ReceiptDetail; // keys are in format "image_id_receipt_id"
  };
  last_evaluated_key: {
    PK: { S: string };
    SK: { S: string };
    GSI2PK: { S: string };
    GSI2SK: { S: string };
  } | null;
}

export interface ReceiptPayloadEntry {
  receipt?: Receipt; // The single "receipt" object
  words?: ReceiptWord[]; // The array of recognized word objects
}

export interface ImageApiResponse {
  payload: RootPayload;
  last_evaluated_key: null | string;
}
export interface LastEvaluatedKey {
  PK?: { S: string };
  SK?: { S: string };
  GSI2PK?: { S: string };
  GSI2SK?: { S: string };
}
export interface ReceiptPayload {
  [receiptId: string]: ReceiptPayloadEntry;
}

export type ImageReceiptsLines = [ImagePayload, Receipt[], LineItem[]];
export interface ReceiptApiResponse {
  receipts: Receipt[];
  lastEvaluatedKey?: string; // This key is used for pagination (if present)
}
export interface ReceiptWordsApiResponse {
  words: ReceiptWord[];
  lastEvaluatedKey?: string; // If there are more pages, this will be a string (often JSON-encoded)
}

export interface ImageDetailsApiResponse {
  images: Image[];
  words: Word[];
  receipts: Receipt[];
  receipt_words: ReceiptWord[];
}

export interface ReceiptWordTagsPayloadItem {
  word: ReceiptWord;
  tag: ReceiptWordTag;
}

export type ReceiptWordTagsPayload = ReceiptWordTagsPayloadItem[];

export interface ReceiptWordTagsApiResponse {
  payload: ReceiptWordTagsPayload;
  last_evaluated_key?: string;
}

export interface TagStats {
  valid: number;
  invalid: number;
  total: number;
}

export interface TagValidationStatsResponse {
  tag_stats: {
    [tag: string]: TagStats;
  }
}

// Example response:
// {
//   "tag_stats": {
//     "total": { "valid": 45, "invalid": 3, "total": 48 },
//     "date": { "valid": 32, "invalid": 1, "total": 33 }
//   }
// }

// Single receipt detail structure
export interface ReceiptDetail {
  receipt: Receipt;
  words: ReceiptWord[];
  word_tags: ReceiptWordTag[];
}

export interface ReceiptWordTagApiResponse {
  statusCode: number;
  body: string;
}
