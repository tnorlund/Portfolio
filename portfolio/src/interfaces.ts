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
  
  export interface Receipt {
    id: number;
    image_id: number;
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

  export interface LastEvaluatedKey {

  };

  export interface ReceiptWord {
    // IDs indicating what this word is linked to
    image_id: number;
    line_id: number;
    receipt_id: number;
    id: number;
  
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

  export interface ReceiptPayloadEntry {
    receipt?: Receipt;   // The single “receipt” object
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
  export type ReceiptApiResponse = Receipt[];
  export type ReceiptWordApiResponse = ReceiptWord[];