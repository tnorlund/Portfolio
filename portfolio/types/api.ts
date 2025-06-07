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

export interface Point {
  x: number;
  y: number;
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
}
