import { render } from "@testing-library/react";
import { act } from "@testing-library/react";
import React from "react";
import ConvexHull from "../ConvexHull";

describe("ConvexHull", () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
  });

  test("renders hull points sequentially", () => {
    const points = [
      { x: 0, y: 0 },
      { x: 0.5, y: 0.5 },
      { x: 1, y: 1 },
    ];
    const { container } = render(
      <svg>
        <ConvexHull hullPoints={points} svgWidth={100} svgHeight={100} delay={0} />
      </svg>,
    );

    expect(container.querySelectorAll("circle")).toHaveLength(0);

    act(() => {
      jest.advanceTimersByTime(200);
    });
    expect(container.querySelectorAll("circle")).toHaveLength(1);

    act(() => {
      jest.advanceTimersByTime(400);
    });
    expect(container.querySelectorAll("circle")).toHaveLength(points.length);
  });
});
