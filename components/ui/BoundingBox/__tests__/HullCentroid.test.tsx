import { render } from "@testing-library/react";
import React from "react";
import HullCentroid from "../HullCentroid";

jest.mock("@react-spring/web", () => ({
  useSpring: () => ({
    opacity: 1,
    scale: { to: (fn: (v: number) => any) => fn(1) },
  }),
  animated: {
    circle: (props: any) => <circle {...props} />,
  },
}));

test("renders centroid circle at correct position", () => {
  const { container } = render(
    <svg>
      <HullCentroid
        centroid={{ x: 0.5, y: 0.25 }}
        svgWidth={100}
        svgHeight={200}
        delay={0}
      />
    </svg>,
  );

  const circle = container.querySelector("circle");
  expect(circle).toHaveAttribute("cx", "50");
  expect(circle).toHaveAttribute("cy", "150");
});
