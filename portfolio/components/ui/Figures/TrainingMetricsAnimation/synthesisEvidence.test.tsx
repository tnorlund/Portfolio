import { render, screen, waitFor } from "@testing-library/react";
import TrainingMetricsAnimation from ".";
import { api } from "../../../../services/api";
import { TrainingMetricsResponse } from "../../../../types/api";

jest.mock("react-intersection-observer", () => ({
  useInView: () => ({
    ref: jest.fn(),
    inView: true,
  }),
}));

jest.mock("@react-spring/web", () => {
  const React = require("react");
  const springValue = (value: number) => ({
    to: (formatter: (value: number) => string) => formatter(value),
  });

  return {
    animated: {
      div: React.forwardRef((props: any, ref: any) => (
        <div ref={ref} {...props} />
      )),
      span: React.forwardRef((props: any, ref: any) => (
        <span ref={ref} {...props} />
      )),
    },
    useSpring: ({ to }: { to: { value: number; width: number } }) => ({
      value: springValue(to.value),
      width: springValue(to.width),
    }),
  };
});

jest.mock("../../../../services/api", () => ({
  api: {
    fetchFeaturedTrainingMetrics: jest.fn(),
  },
}));

const mockedApi = api as jest.Mocked<typeof api>;

beforeEach(() => {
  mockedApi.fetchFeaturedTrainingMetrics.mockReset();
});

const trainingMetrics = (): TrainingMetricsResponse => ({
  job_id: "layoutlm-local",
  job_name: "layoutlm-local",
  status: "completed",
  created_at: "2026-06-23T00:00:00Z",
  best_epoch: 1,
  best_f1: 0.92,
  total_epochs: 2,
  epochs: [
    {
      epoch: 0,
      is_best: false,
      metrics: {
        val_f1: 0.89,
        val_precision: 0.9,
        val_recall: 0.88,
        train_loss: 0.42,
        eval_loss: 0.39,
      },
      per_label: {},
    },
    {
      epoch: 1,
      is_best: true,
      metrics: {
        val_f1: 0.92,
        val_precision: 0.93,
        val_recall: 0.91,
        train_loss: 0.31,
        eval_loss: 0.33,
      },
      per_label: {},
    },
  ],
  synthesis: {
    status: "available",
    synthetic_train_examples: 1,
    synthetic_candidates_seen: 2,
    synthetic_candidates_accepted: 1,
    synthetic_candidates_rejected: 1,
    bundle_rejection_reasons: {
      accepted_synthetic_mix_single_merchant_high_risk: 1,
    },
    accepted_mix_balance: {
      accepted_count: 1,
      merchant_count: 1,
      operation_count: 1,
      top_merchant: "Sprouts Farmers Market",
      top_merchant_count: 1,
      top_merchant_share: 1,
      top_operation: "add_line_item",
      top_operation_count: 1,
      top_operation_share: 1,
      merchant_entropy: 0,
      operation_entropy: 0,
      risk_level: "high",
      risk_reasons: ["single_merchant_accepted"],
    },
    llm_execution: {
      mode_counts: { deterministic_fallback: 1 },
      paid_llm_disabled_count: 1,
      api_call_allowed_count: 0,
      configured_models: ["openai/gpt-5.5"],
      latest_model_sources: [
        "https://developers.openai.com/api/docs/guides/latest-model",
      ],
      latest_model_verified_at: "2026-06-23",
    },
    merchant_synthesis_contracts: [
      {
        merchant_name: "Sprouts Farmers Market",
        status: "ready",
      },
      {
        merchant_name: "Market Mart",
        status: "ready",
      },
    ],
    quality_report: {
      training_ready: false,
      training_ready_reasons: [
        "cover_ready_operations_before_training",
        "complete_source_lineage_before_training",
      ],
      summary: {
        accepted_source_lineage: {
          schema_version: "accepted-source-lineage-v1",
          coverage_status: "sampled",
          authoritative: false,
          coverage_warning: "sampled_source_lineage_not_authoritative",
          candidate_count: 1,
          observed_candidate_count: 1,
          expected_candidate_count: 2,
          source_receipt_key_count: 4,
          source_receipt_keys_redacted: true,
          with_cross_receipt_item_count: 1,
          with_category_evidence_count: 1,
          with_nearest_real_structure_count: 1,
          with_layout_integrity_count: 1,
          with_arithmetic_reconciliation_count: 1,
        },
      },
      quality_gates: {
        llm_model_freshness_gate: {
          enabled: true,
          passed: true,
          requires_current_model_guidance: false,
          api_call_allowed_count: 0,
          latest_model_verified_at: "2026-06-23",
          max_age_days: 30,
          latest_model_sources: [
            "https://developers.openai.com/api/docs/guides/latest-model",
          ],
        },
      },
      accepted_operation_coverage: {
        operation_count: 4,
        ready_operation_count: 3,
        accepted_operation_count: 2,
        accepted_ready_operation_count: 2,
        accepted_ready_operation_share: 0.667,
        uncovered_ready_operations: ["replace_field"],
      },
      operation_coverage: {
        operation_count: 4,
        ready_operation_count: 3,
      },
    },
  },
});

describe("TrainingMetricsAnimation synthesis evidence", () => {
  it("labels high-risk accepted synthetic merchant mix gates", async () => {
    mockedApi.fetchFeaturedTrainingMetrics.mockResolvedValue(trainingMetrics());

    render(<TrainingMetricsAnimation />);

    await waitFor(() => {
      expect(mockedApi.fetchFeaturedTrainingMetrics).toHaveBeenCalledTimes(1);
    });

    expect(await screen.findByText("1 rejected")).toHaveAttribute(
      "title",
      "1 single merchant mix",
    );

    const mixBalanceValue = screen.getByText("High risk");
    expect(mixBalanceValue).toHaveAttribute(
      "title",
      expect.stringContaining("Top merchant: Sprouts Farmers Market (100%)"),
    );
    expect(mixBalanceValue).toHaveAttribute(
      "title",
      expect.stringContaining("Reasons: single merchant accepted"),
    );

    expect(screen.getByText("2 / 2 ready")).toHaveAttribute(
      "title",
      expect.stringContaining("Ready ops accepted: 2 / 3"),
    );
    expect(screen.getByText("2 / 2 ready")).toHaveAttribute(
      "title",
      expect.stringContaining("Uncovered ready ops: Field edits"),
    );
    expect(screen.getByText("2 / 2 ready")).toHaveAttribute(
      "title",
      expect.stringContaining("Training hold: cover ready operations before training"),
    );
    expect(screen.getByText("2 / 2 ready")).toHaveAttribute(
      "title",
      expect.stringContaining("complete source lineage before training"),
    );
    expect(screen.getByText("Lineage (not auth)")).toHaveAttribute(
      "title",
      expect.stringContaining("Source lineage is sampled and not authoritative"),
    );
    expect(screen.getByText("sampled 1 / 2")).toHaveAttribute(
      "title",
      expect.stringContaining("Source lineage is sampled and not authoritative"),
    );
    expect(screen.getByText("sampled 1 / 2")).toHaveAttribute(
      "title",
      expect.stringContaining("Source receipts: 4 (IDs redacted)"),
    );
    expect(screen.getByText("sampled 1 / 2")).toHaveAttribute(
      "title",
      expect.stringContaining("Arithmetic evidence: 1"),
    );
    expect(screen.getByText("Local only")).toHaveAttribute(
      "title",
      expect.stringContaining("Model guidance: fresh"),
    );
  });

  it("surfaces loader-only accepted operation coverage", async () => {
    const metrics = trainingMetrics();
    metrics.synthesis = {
      status: "metrics_only",
      synthetic_train_examples: 2,
      synthetic_candidates_seen: 3,
      synthetic_candidates_accepted: 2,
      accepted_source_lineage: {
        schema_version: "accepted-source-lineage-v1",
        coverage_status: "complete",
        authoritative: false,
        candidate_count: 2,
        observed_candidate_count: 2,
        source_receipt_key_count: 3,
      },
      accepted_operation_coverage: {
        operation_count: 4,
        ready_operation_count: 2,
        accepted_operation_count: 1,
        accepted_ready_operation_count: 1,
        accepted_ready_operation_share: 0.5,
        uncovered_ready_operations: ["add_line_item"],
      },
    };
    mockedApi.fetchFeaturedTrainingMetrics.mockResolvedValue(metrics);

    render(<TrainingMetricsAnimation />);

    await waitFor(() => {
      expect(mockedApi.fetchFeaturedTrainingMetrics).toHaveBeenCalledTimes(1);
    });

    const contractValue = await screen.findByText("1 / 2 accepted");
    expect(contractValue).toHaveAttribute(
      "title",
      expect.stringContaining("Ready ops accepted: 1 / 2"),
    );
    expect(screen.getByText("not auth 2")).toHaveAttribute(
      "title",
      expect.stringContaining("Source lineage is not authoritative"),
    );
    expect(screen.getByText("not auth 2")).not.toHaveAttribute(
      "title",
      expect.stringContaining("Coverage: 2 / 2"),
    );
    expect(contractValue).toHaveAttribute(
      "title",
      expect.stringContaining("Uncovered ready ops: Add Line Item"),
    );
  });

  it("renders authoritative source lineage as accepted candidate coverage", async () => {
    const metrics = trainingMetrics();
    metrics.synthesis = {
      ...metrics.synthesis!,
      accepted_source_lineage: {
        schema_version: "accepted-source-lineage-v1",
        coverage_status: "complete",
        authoritative: true,
        candidate_count: 1,
        observed_candidate_count: 1,
        expected_candidate_count: 1,
        source_receipt_key_count: 2,
      },
    };
    mockedApi.fetchFeaturedTrainingMetrics.mockResolvedValue(metrics);

    render(<TrainingMetricsAnimation />);

    await waitFor(() => {
      expect(mockedApi.fetchFeaturedTrainingMetrics).toHaveBeenCalledTimes(1);
    });

    expect(await screen.findByText("1 candidate")).toHaveAttribute(
      "title",
      expect.stringContaining("Source lineage covers accepted candidates"),
    );
    expect(screen.getByText("Lineage")).toHaveAttribute(
      "title",
      expect.stringContaining("Coverage: 1 / 1"),
    );
  });

  it("does not use candidate_count as sampled observed coverage", async () => {
    const metrics = trainingMetrics();
    metrics.synthesis = {
      ...metrics.synthesis!,
      accepted_source_lineage: {
        schema_version: "accepted-source-lineage-v1",
        coverage_status: "sampled",
        authoritative: false,
        candidate_count: 5,
        expected_candidate_count: 5,
        source_receipt_key_count: 2,
      },
    };
    mockedApi.fetchFeaturedTrainingMetrics.mockResolvedValue(metrics);

    render(<TrainingMetricsAnimation />);

    await waitFor(() => {
      expect(mockedApi.fetchFeaturedTrainingMetrics).toHaveBeenCalledTimes(1);
    });

    expect(await screen.findByText("sampled expected 5")).toHaveAttribute(
      "title",
      expect.stringContaining("Observed candidates: unavailable / 5 expected"),
    );
    expect(screen.queryByText("sampled 5 / 5")).not.toBeInTheDocument();
  });

  it("flags unverified lineage status and mismatched complete counts", async () => {
    const metrics = trainingMetrics();
    metrics.synthesis = {
      ...metrics.synthesis!,
      accepted_source_lineage: {
        schema_version: "accepted-source-lineage-v1",
        coverage_status: "complete",
        authoritative: true,
        candidate_count: 5,
        observed_candidate_count: 3,
        expected_candidate_count: 5,
        source_receipt_key_count: 4,
      },
    };
    mockedApi.fetchFeaturedTrainingMetrics.mockResolvedValue(metrics);

    render(<TrainingMetricsAnimation />);

    await waitFor(() => {
      expect(mockedApi.fetchFeaturedTrainingMetrics).toHaveBeenCalledTimes(1);
    });

    expect(await screen.findByText("Lineage (gap)")).toHaveAttribute(
      "title",
      expect.stringContaining("count does not match expected coverage"),
    );
    expect(screen.getByText("gap 3 / 5")).toHaveAttribute(
      "title",
      expect.stringContaining("Coverage: 3 / 5"),
    );
  });

  it("flags unknown source lineage coverage status", async () => {
    const metrics = trainingMetrics();
    metrics.synthesis = {
      ...metrics.synthesis!,
      accepted_source_lineage: {
        schema_version: "accepted-source-lineage-v1",
        coverage_status: "partial",
        authoritative: true,
        candidate_count: 2,
        observed_candidate_count: 2,
        expected_candidate_count: 2,
        source_receipt_key_count: 3,
      },
    };
    mockedApi.fetchFeaturedTrainingMetrics.mockResolvedValue(metrics);

    render(<TrainingMetricsAnimation />);

    await waitFor(() => {
      expect(mockedApi.fetchFeaturedTrainingMetrics).toHaveBeenCalledTimes(1);
    });

    expect(await screen.findByText("Lineage (review)")).toHaveAttribute(
      "title",
      expect.stringContaining("coverage status needs review"),
    );
    expect(screen.getByText("review 2 / 2")).toHaveAttribute(
      "title",
      expect.stringContaining("Coverage: 2 / 2"),
    );
  });

  it("flags unsupported source lineage schema versions", async () => {
    const metrics = trainingMetrics();
    metrics.synthesis = {
      ...metrics.synthesis!,
      accepted_source_lineage: {
        schema_version: "accepted-source-lineage-v2",
        coverage_status: "complete",
        authoritative: true,
        candidate_count: 2,
        observed_candidate_count: 2,
        expected_candidate_count: 2,
        source_receipt_key_count: 3,
      },
    };
    mockedApi.fetchFeaturedTrainingMetrics.mockResolvedValue(metrics);

    render(<TrainingMetricsAnimation />);

    await waitFor(() => {
      expect(mockedApi.fetchFeaturedTrainingMetrics).toHaveBeenCalledTimes(1);
    });

    expect(await screen.findByText("schema review")).toHaveAttribute(
      "title",
      expect.stringContaining("unsupported (accepted-source-lineage-v2)"),
    );
    expect(screen.getByText("Lineage (schema)")).toHaveAttribute(
      "title",
      expect.stringContaining("unsupported (accepted-source-lineage-v2)"),
    );
  });
});
