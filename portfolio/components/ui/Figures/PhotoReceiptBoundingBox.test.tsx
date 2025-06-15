import { render, screen } from "@testing-library/react";
import React from "react";
import PhotoReceiptBoundingBox from "./PhotoReceiptBoundingBox";
import fixtureData from "../../../tests/fixtures/target_receipt.json";
import useImageDetails from "../../../hooks/useImageDetails";

jest.mock("../../../hooks/useImageDetails");

jest.mock("../animations", () => ({
  AnimatedConvexHull: () => <g data-testid="AnimatedConvexHull" />,
  AnimatedHullCentroid: () => <g data-testid="AnimatedHullCentroid" />,
  AnimatedOrientedAxes: () => <g data-testid="AnimatedOrientedAxes" />,
  AnimatedPrimaryEdges: () => <g data-testid="AnimatedPrimaryEdges" />,
  AnimatedSecondaryBoundaryLines: () => (
    <g data-testid="AnimatedSecondaryBoundaryLines" />
  ),
  AnimatedPrimaryBoundaryLines: () => (
    <g data-testid="AnimatedPrimaryBoundaryLines" />
  ),
  AnimatedReceiptFromHull: () => <g data-testid="AnimatedReceiptFromHull" />,
  AnimatedLineBox: () => <g data-testid="AnimatedLineBox" />,
}));

jest.mock("../../../hooks/useOptimizedInView", () => ({
  __esModule: true,
  default: () => [React.createRef(), true] as const,
}));

const mockedUseImageDetails = useImageDetails as jest.Mock;

describe("PhotoReceiptBoundingBox", () => {
  beforeEach(() => {
    mockedUseImageDetails.mockReturnValue({
      imageDetails: fixtureData,
      formatSupport: { supportsAVIF: true, supportsWebP: true },
      error: null,
      loading: false,
    });
  });

  test("renders polygons for each line", () => {
    render(<PhotoReceiptBoundingBox />);
    const polygons = document.querySelectorAll("polygon");
    expect(polygons).toHaveLength(fixtureData.lines.length);
  });

  test("shows animated convex hull", () => {
    render(<PhotoReceiptBoundingBox />);
    expect(screen.getByTestId("AnimatedConvexHull")).toBeInTheDocument();
  });
});
