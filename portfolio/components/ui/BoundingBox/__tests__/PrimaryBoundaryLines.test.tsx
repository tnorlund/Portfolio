import { render } from "@testing-library/react";
import React from "react";
import PrimaryBoundaryLines, { LineSegment } from "../PrimaryBoundaryLines";

jest.mock("@react-spring/web", () => ({
  useTransition:
    (items: any[]) => (fn: (style: any, item: any) => React.ReactElement) =>
      items.map((item) => fn({}, item)),
  animated: {
    line: (props: any) => <line {...props} />,
  },
}));

test("renders circles and lines for segments", () => {
  const segments: LineSegment[] = [
    { x1: 0, y1: 0, x2: 10, y2: 10, key: "a" },
    { x1: 10, y1: 10, x2: 20, y2: 20, key: "b" },
  ];
  const { container } = render(
    <svg>
      <PrimaryBoundaryLines segments={segments} delay={0} />
    </svg>,
  );

  expect(container.querySelectorAll("circle")).toHaveLength(segments.length * 2);
  expect(container.querySelectorAll("line")).toHaveLength(segments.length);
});
