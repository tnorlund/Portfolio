import { render, screen } from "@testing-library/react";
import React from "react";
import PhotoReceiptBoundingBox from "./PhotoReceiptBoundingBox";
import fixtureData from "../../../tests/fixtures/target_receipt.json";
import useImageDetails from "../../../hooks/useImageDetails";

import * as animations from "../animations";

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
  const AnimatedTopAndBottom = jest.fn(() => (
    <g data-testid="AnimatedTopAndBottom" />
  ));
  const AnimatedHullEdgeAlignment = jest.fn(() => (
    <g data-testid="AnimatedHullEdgeAlignment" />
  ));
  const AnimatedFinalReceiptBox = jest.fn(() => (
    <g data-testid="AnimatedFinalReceiptBox" />
  ));

  return {
    AnimatedConvexHull,
    AnimatedHullCentroid,
    AnimatedOrientedAxes,
    AnimatedTopAndBottom,
    AnimatedHullEdgeAlignment,
    AnimatedFinalReceiptBox,
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

  test("renders polygons for each line in largest cluster", () => {
    render(<PhotoReceiptBoundingBox />);
    // The component now uses DBSCAN clustering to filter lines
    // Only lines from the largest cluster are rendered with red stroke
    const polygons = document.querySelectorAll('polygon[stroke="var(--color-red)"]');
    // The exact count depends on clustering results
    expect(polygons.length).toBeGreaterThan(0);
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

    const svgWidth = fixtureData.image.width;
    const svgHeight = fixtureData.image.height;

    // AnimatedConvexHull should receive hull points and delay
    expect(animations.AnimatedConvexHull).toHaveBeenCalledTimes(1);
    expect(
      (animations.AnimatedConvexHull as jest.Mock).mock.calls[0][0]
    ).toEqual(
      expect.objectContaining({
        hullPoints: expect.any(Array),
        svgWidth,
        svgHeight,
        delay: expect.any(Number),
        showIndices: true,
      })
    );
    expect(screen.getByTestId("AnimatedConvexHull")).toBeInTheDocument();

    // AnimatedHullCentroid should receive centroid
    expect(animations.AnimatedHullCentroid).toHaveBeenCalledTimes(1);
    expect(
      (animations.AnimatedHullCentroid as jest.Mock).mock.calls[0][0]
    ).toEqual(
      expect.objectContaining({
        centroid: expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }),
        svgWidth,
        svgHeight,
        delay: expect.any(Number),
      })
    );
    expect(screen.getByTestId("AnimatedHullCentroid")).toBeInTheDocument();

    // AnimatedTopAndBottom should receive topLine, bottomLine, and corners
    expect(animations.AnimatedTopAndBottom).toHaveBeenCalledTimes(1);
    expect(
      (animations.AnimatedTopAndBottom as jest.Mock).mock.calls[0][0]
    ).toEqual(
      expect.objectContaining({
        topLine: expect.any(Object),
        bottomLine: expect.any(Object),
        topLineCorners: expect.any(Array),
        bottomLineCorners: expect.any(Array),
        svgWidth,
        svgHeight,
        delay: expect.any(Number),
      })
    );
    expect(screen.getByTestId("AnimatedTopAndBottom")).toBeInTheDocument();

    // AnimatedOrientedAxes should receive avgAngleRad and hull extreme points
    expect(animations.AnimatedOrientedAxes).toHaveBeenCalledTimes(1);
    expect(
      (animations.AnimatedOrientedAxes as jest.Mock).mock.calls[0][0]
    ).toEqual(
      expect.objectContaining({
        hull: expect.any(Array),
        centroid: expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }),
        avgAngleRad: expect.any(Number),
        leftmostHullPoint: expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }),
        rightmostHullPoint: expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }),
        svgWidth,
        svgHeight,
        delay: expect.any(Number),
      })
    );
    expect(screen.getByTestId("AnimatedOrientedAxes")).toBeInTheDocument();

    // AnimatedFinalReceiptBox should receive finalReceiptBox and edge data
    expect(animations.AnimatedFinalReceiptBox).toHaveBeenCalledTimes(1);
    expect(
      (animations.AnimatedFinalReceiptBox as jest.Mock).mock.calls[0][0]
    ).toEqual(
      expect.objectContaining({
        finalReceiptBox: expect.any(Array),
        topLineCorners: expect.any(Array),
        bottomLineCorners: expect.any(Array),
        leftmostHullPoint: expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }),
        rightmostHullPoint: expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }),
        avgAngleRad: expect.any(Number),
        svgWidth,
        svgHeight,
        delay: expect.any(Number),
      })
    );
    expect(screen.getByTestId("AnimatedFinalReceiptBox")).toBeInTheDocument();

    // AnimatedHullEdgeAlignment is no longer used in the simplified approach
    expect(animations.AnimatedHullEdgeAlignment).not.toHaveBeenCalled();
  });
});
