import { render } from "@testing-library/react";
import { act } from "@testing-library/react";
import React from "react";
import OrientedAxes from "../OrientedAxes";

const line = {
  image_id: "1",
  line_id: 1,
  text: "",
  bounding_box: { x: 0, y: 0, width: 1, height: 1 },
  top_left: { x: 0, y: 1 },
  top_right: { x: 1, y: 1 },
  bottom_left: { x: 0, y: 0 },
  bottom_right: { x: 1, y: 0 },
  angle_degrees: 0,
  angle_radians: 0,
  confidence: 1,
};

describe("OrientedAxes", () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
  });

  test("draws axes and extreme points", () => {
    const hull = [
      { x: 0, y: 0 },
      { x: 0, y: 1 },
      { x: 1, y: 0 },
    ];
    const centroid = { x: 0.5, y: 0.5 };
    const { container } = render(
      <svg>
        <OrientedAxes
          hull={hull}
          centroid={centroid}
          lines={[line] as any}
          svgWidth={100}
          svgHeight={100}
          delay={0}
        />
      </svg>,
    );

    expect(container.querySelectorAll("line")).toHaveLength(0);
    act(() => {
      jest.advanceTimersByTime(400);
    });
    expect(container.querySelectorAll("line")).toHaveLength(1);
    act(() => {
      jest.advanceTimersByTime(400);
    });
    expect(container.querySelectorAll("line")).toHaveLength(2);
    act(() => {
      jest.advanceTimersByTime(400);
    });
    expect(container.querySelectorAll("circle")).toHaveLength(4);
  });
});
