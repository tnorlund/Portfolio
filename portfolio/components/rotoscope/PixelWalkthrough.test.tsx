import { act, fireEvent, render, screen } from "@testing-library/react";
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

const originalMatchMedia = window.matchMedia;

const mockMatchMedia = (matches: (query: string) => boolean) => {
  window.matchMedia = jest.fn().mockImplementation((query: string) => ({
    matches: matches(query),
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  }));
};

afterEach(() => {
  window.matchMedia = originalMatchMedia;
});

test("walks grayscale, blur, difference, and Sobel with kernel arithmetic", () => {
  render(<PixelWalkthrough source={source} blurRadius={1} />);

  expect(screen.getByRole("button", { name: "Show Grayscale step" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByLabelText("Source pixels around the sample")).toBeInTheDocument();
  expect(screen.getByText("pulled pixel")).toBeInTheDocument();
  expect(screen.getByLabelText("Red, green, and blue of the pixel")).toBeInTheDocument();
  expect(screen.getByLabelText("Resulting grayscale value")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Replay the zoom" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Show Box blur step" }));
  expect(screen.getByLabelText("Box-blur kernel")).toBeInTheDocument();
  expect(screen.getByLabelText("Gray pixels under the box-blur kernel")).toBeInTheDocument();
  expect(screen.getByText(/3×3 box, radius 1/)).toBeInTheDocument();
  expect(screen.getByText(/Average the row, then the column/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Show Difference step" }));
  expect(screen.getByLabelText("Gray, blur, and absolute difference")).toBeInTheDocument();
  expect(screen.getByLabelText("Gray pixels around the sample")).toBeInTheDocument();
  expect(screen.getByLabelText("Blurred pixels around the sample")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Show Sobel step" }));
  expect(screen.getByLabelText("Sobel Gx kernel")).toBeInTheDocument();
  expect(screen.getByLabelText("Sobel Gy kernel")).toBeInTheDocument();
  expect(screen.getByLabelText("Difference neighborhood")).toBeInTheDocument();
  expect(screen.getByLabelText("Gray neighborhood")).toBeInTheDocument();
  expect(screen.getByLabelText("Sobel magnitudes on both landscapes")).toBeInTheDocument();
});

test("clicking a zoomed pixel picks it and pauses the sampler", () => {
  render(<PixelWalkthrough source={source} blurRadius={1} />);
  fireEvent.click(
    screen.getByRole("button", {
      name: /Source pixels around the sample, offset 1, 0, value/,
    }),
  );
  expect(screen.getByText(/pixel \d+, \d+/)).toBeInTheDocument();
});

test("uses a 3×3 neighborhood on a narrow viewport", () => {
  mockMatchMedia((query) => query.includes("max-width: 768px"));
  render(<PixelWalkthrough source={source} blurRadius={3} />);

  const board = screen.getByLabelText("Source pixels around the sample");
  expect(board).toHaveAttribute("data-size", "3");
  expect(board.querySelectorAll("button")).toHaveLength(9);
  expect(screen.getByText(/3×3 of 7×7 source/)).toBeInTheDocument();
  expect(screen.getByText(/3×3 center of the 7×7 neighborhood/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Show Box blur step" }));
  expect(screen.getByLabelText("Box-blur kernel")).toHaveTextContent("7×7 box, radius 3");
  expect(screen.getByLabelText("Gray pixels under the box-blur kernel")).toHaveAttribute(
    "data-size",
    "3",
  );
});

test("zooms from the photograph into the pixel grid", () => {
  jest.useFakeTimers();
  try {
    render(<PixelWalkthrough source={source} blurRadius={1} skipIntro={false} />);
    const stage = screen.getByTestId("walk-zoom-stage");
    expect(stage).toHaveAttribute("data-phase", "photo");
    expect(
      screen.getByRole("img", {
        name: "Original portrait; the highlighted window becomes the pixel grid",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Replay the zoom" })).toBeInTheDocument();

    act(() => {
      jest.advanceTimersByTime(800);
    });
    expect(stage).toHaveAttribute("data-phase", "frame");
    act(() => {
      jest.advanceTimersByTime(650);
    });
    expect(stage).toHaveAttribute("data-phase", "zoom");
    act(() => {
      jest.advanceTimersByTime(1100);
    });
    expect(stage).toHaveAttribute("data-phase", "pixels");

    fireEvent.click(screen.getByRole("button", { name: "Replay the zoom" }));
    expect(stage).toHaveAttribute("data-phase", "photo");
  } finally {
    jest.useRealTimers();
  }
});
