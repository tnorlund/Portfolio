/**
 * Minimal mock data for TrainingMetricsAnimation e2e tests.
 * Matches the TrainingMetricsResponse interface.
 */

const LABELS = [
  "MERCHANT_NAME",
  "DATE",
  "TIME",
  "AMOUNT",
  "ADDRESS",
  "WEBSITE",
  "STORE_HOURS",
  "PAYMENT_METHOD",
  "O",
];

function makePerLabel(
  base: number
): Record<string, { f1: number; precision: number; recall: number; support: number }> {
  const out: Record<string, { f1: number; precision: number; recall: number; support: number }> = {};
  for (const label of LABELS) {
    out[label] = {
      f1: base + Math.random() * 0.1,
      precision: base + Math.random() * 0.1,
      recall: base + Math.random() * 0.1,
      support: Math.floor(50 + Math.random() * 200),
    };
  }
  return out;
}

function makeConfusionMatrix() {
  const n = LABELS.length;
  const matrix: number[][] = [];
  for (let i = 0; i < n; i++) {
    const row: number[] = [];
    for (let j = 0; j < n; j++) {
      row.push(i === j ? 80 + Math.floor(Math.random() * 20) : Math.floor(Math.random() * 5));
    }
    matrix.push(row);
  }
  return { labels: LABELS, matrix };
}

export const mockTrainingMetricsResponse = {
  job_id: "test-job-id",
  job_name: "layoutlm-test-run",
  status: "Completed",
  created_at: "2025-01-15T10:00:00Z",
  dataset_metrics: {
    num_train_samples: 1200,
    num_val_samples: 300,
    o_entity_ratio_train: 0.72,
    o_entity_ratio_val: 0.71,
    random_seed: 42,
    num_train_receipts: 150,
    num_val_receipts: 38,
  },
  epochs: Array.from({ length: 10 }, (_, i) => ({
    epoch: i + 1,
    is_best: i === 7,
    metrics: {
      val_f1: 0.5 + i * 0.04,
      val_precision: 0.5 + i * 0.04,
      val_recall: 0.5 + i * 0.03,
      eval_loss: 0.8 - i * 0.06,
      train_loss: 0.9 - i * 0.07,
      learning_rate: 5e-5 * (1 - i / 10),
    },
    confusion_matrix: makeConfusionMatrix(),
    per_label: makePerLabel(0.5 + i * 0.04),
  })),
  best_epoch: 8,
  best_f1: 0.82,
  total_epochs: 10,
};
