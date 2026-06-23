import React from "react";
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
      training_ready_reasons: ["cover_ready_operations_before_training"],
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
  });

  it("surfaces loader-only accepted operation coverage", async () => {
    const metrics = trainingMetrics();
    metrics.synthesis = {
      status: "metrics_only",
      synthetic_train_examples: 2,
      synthetic_candidates_seen: 3,
      synthetic_candidates_accepted: 2,
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
    expect(contractValue).toHaveAttribute(
      "title",
      expect.stringContaining("Uncovered ready ops: Add Line Item"),
    );
  });
});
