import { render, screen } from "@testing-library/react";
import { rest, setupServer } from "../../test-utils/msw";
import React from "react";
import PhotoReceiptBoundingBox from "../../components/ui/Figures/PhotoReceiptBoundingBox";
import fixtureData from "../../tests/fixtures/target_receipt.json";

jest.mock("../../components/ui/animations", () => ({
  AnimatedConvexHull: () => <g data-testid="AnimatedConvexHull" />,
  AnimatedHullCentroid: () => <g data-testid="AnimatedHullCentroid" />,
  AnimatedOrientedAxes: () => <g data-testid="AnimatedOrientedAxes" />,
  AnimatedTopAndBottom: () => <g data-testid="AnimatedTopAndBottom" />,
  AnimatedHullEdgeAlignment: () => (
    <g data-testid="AnimatedHullEdgeAlignment" />
  ),
  AnimatedFinalReceiptBox: () => <g data-testid="AnimatedFinalReceiptBox" />,
}));

jest.mock("../../hooks/useOptimizedInView", () => ({
  __esModule: true,
  default: () => [React.createRef(), true] as const,
}));

jest.mock("../../utils/image", () => {
  const actual = jest.requireActual("../../utils/image");
  return {
    ...actual,
    detectImageFormatSupport: () =>
      Promise.resolve({ supportsAVIF: true, supportsWebP: true }),
  };
});

const server = setupServer(
  rest.get(
    "https://api.tylernorlund.com/random_image_details",
    () => fixtureData
  )
);

beforeAll(() => server.listen());
afterEach(() => {
  server.resetHandlers();
});
afterAll(() => server.close());

describe("PhotoReceiptBoundingBox integration", () => {
  test("fetches image details and displays overlays", async () => {
    const fetchSpy = jest.spyOn(global, "fetch");
    render(<PhotoReceiptBoundingBox />);

    expect(
      await screen.findByTestId("AnimatedConvexHull", {}, { timeout: 10000 })
    ).toBeInTheDocument();
    expect(screen.getByTestId("AnimatedHullCentroid")).toBeInTheDocument();

    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining("/random_image_details"),
      expect.any(Object)
    );
  });
});
