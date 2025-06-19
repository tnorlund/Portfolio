import { render, screen } from "@testing-library/react";
import React from "react";
import PhotoReceiptBoundingBox from "./PhotoReceiptBoundingBox";
import fixtureData from "../../../tests/fixtures/target_receipt.json";
import useImageDetails from "../../../hooks/useImageDetails";
import { getAnimationConfig } from "./animationConfig";

import * as animations from "../animations";
import { convexHull, computeHullCentroid } from "../../../utils/geometry";
import { computeFinalReceiptTilt } from "../../../utils/receipt";

jest.mock("../../../hooks/useImageDetails");

jest.mock("../animations", () => {
  const React = require("react");
  const AnimatedConvexHull = jest.fn(() => (
    <g data-testid="AnimatedConvexHull" />
  ));
  const AnimatedHullCentroid = jest.fn(() => (
    <g data-testid="AnimatedHullCentroid" />
  ));
  const AnimatedOrientedAxes = jest.fn(() => (
    <g data-testid="AnimatedOrientedAxes" />
  ));
  const AnimatedPrimaryEdges = jest.fn(() => (
    <g data-testid="AnimatedPrimaryEdges" />
  ));
  const AnimatedSecondaryBoundaryLines = jest.fn(() => (
    <g data-testid="AnimatedSecondaryBoundaryLines" />
  ));
  const AnimatedReceiptFromHull = jest.fn(() => (
    <g data-testid="AnimatedReceiptFromHull" />
  ));
  const AnimatedHullEdgeAlignment = jest.fn(() => (
    <g data-testid="AnimatedHullEdgeAlignment" />
  ));
  const AnimatedLineBox = jest.fn(() => <g data-testid="AnimatedLineBox" />);

  return {
    AnimatedConvexHull,
    AnimatedHullCentroid,
    AnimatedOrientedAxes,
    AnimatedPrimaryEdges,
    AnimatedSecondaryBoundaryLines,
    AnimatedReceiptFromHull,
    AnimatedHullEdgeAlignment,
    AnimatedLineBox,
  };
});

jest.mock("../../../hooks/useOptimizedInView", () => ({
  __esModule: true,
  default: () => [React.createRef(), true] as const,
}));

const mockedUseImageDetails = useImageDetails as jest.Mock;

describe("PhotoReceiptBoundingBox", () => {
  beforeEach(() => {
    jest.clearAllMocks();
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

  test("animated components receive calculated props", async () => {
    const { rerender } = render(<PhotoReceiptBoundingBox />);
    await screen.findByTestId("AnimatedHullCentroid");
    jest.clearAllMocks();
    rerender(<PhotoReceiptBoundingBox />);

    const lines = fixtureData.lines;
    const svgWidth = fixtureData.image.width;
    const svgHeight = fixtureData.image.height;

    const allCorners: { x: number; y: number }[] = [];
    lines.forEach((line) => {
      allCorners.push(
        { x: line.top_left.x, y: line.top_left.y },
        { x: line.top_right.x, y: line.top_right.y },
        { x: line.bottom_right.x, y: line.bottom_right.y },
        { x: line.bottom_left.x, y: line.bottom_left.y }
      );
    });

    const hullPoints = allCorners.length > 2 ? convexHull([...allCorners]) : [];
    const hullCentroid =
      hullPoints.length > 0 ? computeHullCentroid(hullPoints) : null;
    const avgAngle =
      lines.reduce((sum, l) => sum + l.angle_degrees, 0) / lines.length;
    const finalAngle =
      hullCentroid && hullPoints.length > 0
        ? computeFinalReceiptTilt(
            lines as any,
            hullPoints,
            hullCentroid,
            avgAngle
          )
        : avgAngle;

    const {
      totalDelayForLines,
      convexHullDelay,
      convexHullDuration,
      centroidDelay,
      extentsDelay,
    } = getAnimationConfig(lines.length, hullPoints.length);


    expect(animations.AnimatedConvexHull).toHaveBeenCalledTimes(1);
    expect(
      (animations.AnimatedConvexHull as jest.Mock).mock.calls[0][0]
    ).toEqual(
      expect.objectContaining({
        hullPoints,
        svgWidth,
        svgHeight,
        delay: convexHullDelay,
        showIndices: true,
      })
    );
    expect(screen.getByTestId("AnimatedConvexHull")).toBeInTheDocument();

    expect(animations.AnimatedHullCentroid).toHaveBeenCalledTimes(1);
    expect(
      (animations.AnimatedHullCentroid as jest.Mock).mock.calls[0][0]
    ).toEqual(
      expect.objectContaining({
        centroid: hullCentroid,
        svgWidth,
        svgHeight,
        delay: centroidDelay,
      })
    );
    expect(screen.getByTestId("AnimatedHullCentroid")).toBeInTheDocument();

    expect(animations.AnimatedOrientedAxes).toHaveBeenCalledTimes(1);
    expect(
      (animations.AnimatedOrientedAxes as jest.Mock).mock.calls[0][0]
    ).toEqual(
      expect.objectContaining({
        hull: hullPoints,
        centroid: hullCentroid,
        lines,
        svgWidth,
        svgHeight,
        delay: extentsDelay,
      })
    );
    expect(screen.getByTestId("AnimatedOrientedAxes")).toBeInTheDocument();

    expect(animations.AnimatedPrimaryEdges).toHaveBeenCalledTimes(1);
    expect(
      (animations.AnimatedPrimaryEdges as jest.Mock).mock.calls[0][0]
    ).toEqual(
      expect.objectContaining({
        lines,
        hull: hullPoints,
        centroid: hullCentroid,
        avgAngle: finalAngle,
        svgWidth,
        svgHeight,
        delay: extentsDelay + 1000,
      })
    );
    expect(screen.getByTestId("AnimatedPrimaryEdges")).toBeInTheDocument();

    expect(animations.AnimatedSecondaryBoundaryLines).toHaveBeenCalledTimes(1);
    expect(
      (animations.AnimatedSecondaryBoundaryLines as jest.Mock).mock.calls[0][0]
    ).toEqual(
      expect.objectContaining({
        lines,
        hull: hullPoints,
        centroid: hullCentroid,
        avgAngle: avgAngle,
        svgWidth,
        svgHeight,
        delay: extentsDelay + 1500,
      })
    );
    expect(
      screen.getByTestId("AnimatedSecondaryBoundaryLines")
    ).toBeInTheDocument();

  });
});
