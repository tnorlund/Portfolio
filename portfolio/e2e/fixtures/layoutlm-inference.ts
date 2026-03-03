/**
 * Minimal mock data for LayoutLMBatchVisualization e2e tests.
 * Matches the LayoutLMBatchInferenceResponse interface.
 */

function makeReceipt(receiptId: number) {
  const words = Array.from({ length: 20 }, (_, i) => ({
    receipt_id: receiptId,
    line_id: Math.floor(i / 4),
    word_id: i,
    text: ["WALMART", "SUPERCENTER", "123", "Main", "St", "$1.99", "$2.49", "$0.50", "TOTAL", "$4.98", "VISA", "01/15/2025", "10:30", "AM", "Thank", "you", "for", "shopping", "with", "us"][i],
    bounding_box: {
      x: 50 + (i % 4) * 100,
      y: 50 + Math.floor(i / 4) * 40,
      width: 80,
      height: 30,
    },
  }));

  const predictions = words.map((w, i) => ({
    word_id: w.word_id,
    line_id: w.line_id,
    text: w.text,
    predicted_label: ["B-MERCHANT_NAME", "I-MERCHANT_NAME", "B-ADDRESS", "I-ADDRESS", "I-ADDRESS", "B-AMOUNT", "B-AMOUNT", "B-AMOUNT", "O", "B-AMOUNT", "B-PAYMENT_METHOD", "B-DATE", "B-TIME", "I-TIME", "O", "O", "O", "O", "O", "O"][i],
    predicted_label_base: ["MERCHANT_NAME", "MERCHANT_NAME", "ADDRESS", "ADDRESS", "ADDRESS", "AMOUNT", "AMOUNT", "AMOUNT", "O", "AMOUNT", "PAYMENT_METHOD", "DATE", "TIME", "TIME", "O", "O", "O", "O", "O", "O"][i],
    ground_truth_label: null,
    ground_truth_label_base: null,
    predicted_confidence: 0.85 + Math.random() * 0.14,
    is_correct: true,
  }));

  return {
    receipt_id: String(receiptId),
    original: {
      receipt: {
        image_id: `img-${receiptId}`,
        receipt_id: receiptId,
        width: 500,
        height: 700,
        cdn_s3_bucket: "test-bucket",
        cdn_s3_key: `receipts/${receiptId}.jpg`,
      },
      words,
      predictions,
    },
    metrics: {
      overall_accuracy: 0.92,
      total_words: 20,
      correct_predictions: 18,
    },
    model_info: {
      model_name: "layoutlm-test",
      device: "cpu",
      s3_uri: "s3://test/model",
    },
    entities_summary: {
      merchant_name: "WALMART SUPERCENTER",
      date: "01/15/2025",
      address: "123 Main St",
      amount: "$4.98",
    },
    inference_time_ms: 150,
    cached_at: "2025-01-15T10:00:00Z",
  };
}

export const mockLayoutLMInferenceResponse = {
  receipts: [makeReceipt(1), makeReceipt(2), makeReceipt(3)],
  aggregate_stats: {
    avg_accuracy: 0.92,
    min_accuracy: 0.88,
    max_accuracy: 0.95,
    avg_inference_time_ms: 150,
    total_receipts_in_pool: 100,
    batch_size: 3,
    total_words_processed: 60,
    estimated_throughput_per_hour: 2400,
  },
  fetched_at: "2025-01-15T10:00:00Z",
};
