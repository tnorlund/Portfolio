import { fireEvent, render, screen } from "@testing-library/react";
import PixelWalkthrough from "./PixelWalkthrough";

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

test("walks grayscale, blur, difference, and Sobel with kernel arithmetic", () => {
  render(<PixelWalkthrough source={source} blurRadius={1} />);

  expect(screen.getByRole("button", { name: "Show Grayscale step" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByText("pulled pixel")).toBeInTheDocument();
  expect(screen.getByLabelText("Red, green, and blue of the pixel")).toBeInTheDocument();
  expect(screen.getByLabelText("Resulting grayscale value")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Show Box blur step" }));
  expect(screen.getByLabelText("Box-blur kernel")).toBeInTheDocument();
  expect(screen.getByLabelText("Gray values under the kernel")).toBeInTheDocument();
  expect(screen.getByText(/3×3 box, radius 1/)).toBeInTheDocument();
  expect(screen.getByText(/Average the row, then the column/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Show Difference step" }));
  expect(screen.getByLabelText("Gray, blur, and absolute difference")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Show Sobel step" }));
  expect(screen.getByLabelText("Sobel Gx kernel")).toBeInTheDocument();
  expect(screen.getByLabelText("Sobel Gy kernel")).toBeInTheDocument();
  expect(screen.getByLabelText("Difference neighborhood")).toBeInTheDocument();
  expect(screen.getByLabelText("Gray neighborhood")).toBeInTheDocument();
  expect(screen.getByLabelText("Sobel magnitudes on both landscapes")).toBeInTheDocument();
});

test("clicking the portrait picks a pixel and pauses the sampler", () => {
  render(<PixelWalkthrough source={source} blurRadius={1} />);
  const frame = screen.getByRole("button", { name: "The source portrait" });
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
  fireEvent.click(frame, { clientX: 50, clientY: 50 });
  expect(screen.getByText(/pixel \d+, \d+/)).toBeInTheDocument();
});
