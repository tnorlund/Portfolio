import React from "react";
import { EvaluatorCarousel } from "../TwoTierFlow";
import type { CarouselConfig } from "../TwoTierFlow";

const config: CarouselConfig = {
  tier1: {
    name: "Math Validation",
    color: "var(--color-green)",
    type: "Deterministic",
  },
  tier2: {
    name: "ChromaDB Consensus",
    color: "var(--color-purple)",
    type: "Consensus",
  },
  getEvaluation: (receipt) => receipt.financial,
  scanLineColors: ["var(--color-green)", "var(--color-purple)"],
};

const CurrencyVisualization: React.FC = () => {
  return <EvaluatorCarousel config={config} />;
};

export default CurrencyVisualization;
