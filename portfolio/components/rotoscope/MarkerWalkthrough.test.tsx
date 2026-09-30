import { fireEvent, render, screen } from "@testing-library/react";
import MarkerWalkthrough from "./MarkerWalkthrough";

jest.mock("react-intersection-observer", () => ({
  useInView: () => ({ ref: jest.fn(), inView: true }),
}));

const rgba = (
  width: number,
  height: number,
  pixel: (x: number, y: number) => readonly [number, number, number, number],
): Uint8ClampedArray => {
  const output = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      output.set(pixel(x, y), (y * width + x) * 4);
    }
  }
  return output;
};

const source = {
  width: 9,
  height: 9,
  rgba: rgba(9, 9, (x, y) => [x * 24, y * 12, 40, 255]),
};

test("walks score, local max, and tiers", () => {
  render(<MarkerWalkthrough source={source} blurRadius={1} />);

  expect(screen.getByRole("button", { name: "Show Score step" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByLabelText("Shi-Tomasi Gx window")).toBeInTheDocument();
  expect(screen.getByLabelText("Shi-Tomasi Gy window")).toBeInTheDocument();
  expect(screen.getByLabelText("Shi-Tomasi corner score")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Show Local max step" }));
  expect(
    screen.getByLabelText("Shi-Tomasi scores around the pixel"),
  ).toBeInTheDocument();
  expect(screen.getByText(/Kept|Rejected/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Show Tiers step" }));
  expect(screen.getByLabelText("Focus tiers")).toBeInTheDocument();
  expect(screen.getByText("Face 30%")).toBeInTheDocument();
  expect(screen.getByText("Body 64%")).toBeInTheDocument();
  expect(screen.getByText("Background 6%")).toBeInTheDocument();
});

test("clicking the difference image picks a pixel", () => {
  render(<MarkerWalkthrough source={source} blurRadius={1} />);
  const frame = screen.getByRole("button", {
    name: "Difference image that Shi-Tomasi scores",
  });
  frame.getBoundingClientRect = () =>
    ({
      width: 100,
      height: 100,
      left: 0,
      top: 0,
      right: 100,
      bottom: 100,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    }) as DOMRect;
  fireEvent.click(frame, { clientX: 40, clientY: 40 });
  expect(screen.getByText(/pixel \d+, \d+/)).toBeInTheDocument();
});
