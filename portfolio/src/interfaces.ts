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
  
  export interface ApiResponse {
    payload: RootPayload;
    last_evaluated_key: null | string;
  }
  
  export type ImageReceiptsLines = [ImagePayload, Receipt[], LineItem[]];