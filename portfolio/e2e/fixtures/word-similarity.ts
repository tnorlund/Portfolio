import { MilkSimilarityResponse } from "../../types/api";

/**
 * Mock response for word similarity API tests.
 * This represents the structure returned by the word-similarity-cache-generator Lambda.
 */
export const mockWordSimilarityResponse: MilkSimilarityResponse = {
  query_word: "MILK",
  total_receipts: 3,
  total_items: 3,
  summary_table: [
    {
      merchant: "Whole Foods",
      product: "WHOLE MILK",
      size: "Gallon",
      count: 2,
      avg_price: 7.99,
      total: 15.98,
      receipts: [
        { image_id: "test-image-1", receipt_id: 1 },
        { image_id: "test-image-2", receipt_id: 1 },
      ],
    },
    {
      merchant: "Trader Joe's",
      product: "ORG WHOLE MILK",
      size: "Half Gallon",
      count: 1,
      avg_price: 5.49,
      total: 5.49,
      receipts: [{ image_id: "test-image-3", receipt_id: 1 }],
    },
  ],
  receipts: [
    {
      image_id: "test-image-1",
      receipt_id: 1,
      product: "WHOLE MILK",
      merchant: "Whole Foods",
      price: "7.99",
      size: "Gallon",
      line_id: 5,
      receipt: {
        image_id: "test-image-1",
        receipt_id: 1,
        width: 1000,
        height: 2000,
        timestamp_added: "2025-01-01T00:00:00Z",
        raw_s3_bucket: "test-bucket",
        raw_s3_key: "test-key-1.jpg",
        top_left: { x: 0, y: 1 },
        top_right: { x: 1, y: 1 },
        bottom_left: { x: 0, y: 0 },
        bottom_right: { x: 1, y: 0 },
        sha256: "abc123",
        cdn_s3_bucket: "test-cdn-bucket",
        cdn_s3_key: "cdn/test-1.jpg",
        cdn_medium_s3_key: "cdn/test-1-medium.jpg",
      },
      lines: [],
      bbox: {
        tl: { x: 0.1, y: 0.9 },
        tr: { x: 0.9, y: 0.9 },
        bl: { x: 0.1, y: 0.8 },
        br: { x: 0.9, y: 0.8 },
      },
    },
  ],
  cached_at: "2025-01-20T00:00:00Z",
  timing: {
    chromadb_init_ms: 100,
    chromadb_fetch_all_ms: 200,
    filter_lines_ms: 5,
    dynamo_fetch_total_ms: 500,
    total_ms: 850,
    parallel_workers: 50,
    use_chroma_cloud: true,
    cloud_connect_ms: 50,
  },
  grand_total: 21.47,
  commentary:
    '$21.47. That\'s... significantly more than I expected. I knew I liked milk, but I didn\'t think I "21 dollars a year" liked milk.',
};

/**
 * Mock response without commentary (simulates old cache format).
 */
export const mockWordSimilarityResponseWithoutCommentary: MilkSimilarityResponse =
  {
    ...mockWordSimilarityResponse,
    grand_total: undefined,
    commentary: undefined,
  };

/**
 * Mock response with higher spending for different commentary text.
 */
export const mockWordSimilarityHighSpending: MilkSimilarityResponse = {
  ...mockWordSimilarityResponse,
  summary_table: [
    {
      merchant: "Whole Foods",
      product: "RAW WHOLE MILK",
      size: "Gallon",
      count: 50,
      avg_price: 16.99,
      total: 849.50,
      receipts: [],
    },
  ],
  grand_total: 849.50,
  commentary:
    '$849.50. That\'s... significantly more than I expected. I knew I liked milk, but I didn\'t think I "800 dollars a year" liked milk.',
};
