import { fireEvent, render, screen } from "@testing-library/react";
import PixelWalkthrough from "./PixelWalkthrough";

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

// The default sample (the left eye at 0.367, 0.518) lands on pixel (3, 4):
// R 72, G 48, B 40, so gray = (77·72 + 150·48 + 29·40 + 128) >> 8 = 54.
const source = {
  width: 9,
  height: 9,
  rgba: rgba(9, 9, (x, y) => [x * 24, y * 12, 40, 255]),
};

test("follows one window from the photo through every stage with real values", () => {
  render(<PixelWalkthrough source={source} blurRadius={1} />);

  expect(
    screen.getByText(/A 5×5 window at the left eye, followed through the whole chain/),
  ).toBeInTheDocument();
  expect(
    screen.getByAltText(
      "The original portrait; the marked window at the left eye is traced below",
    ),
  ).toBeInTheDocument();

  // Every stage renders the same square window, in order.
  expect(screen.getByLabelText("The 25 source pixels")).toBeInTheDocument();
  expect(screen.getByLabelText("The same pixels as gray values")).toBeInTheDocument();
  expect(
    screen.getByLabelText("The same window after the box blur"),
  ).toBeInTheDocument();
  expect(
    screen.getByLabelText("Gray minus blur, the texture that remains"),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Difference neighborhood")).toBeInTheDocument();
  expect(screen.getByLabelText("Sobel Gx kernel")).toBeInTheDocument();
  expect(screen.getByLabelText("Sobel Gy kernel")).toBeInTheDocument();

  // The arithmetic uses the window's real values.
  expect(screen.getByText(/R 72 · G 48 · B 40/)).toBeInTheDocument();
  expect(
    screen.getByText(/\(77·72 \+ 150·48 \+ 29·40 \+ 128\) ≫ 8 = 54/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/each pixel → mean of its 3×3 neighborhood, so 54 → \d+/),
  ).toBeInTheDocument();
  expect(screen.getByText(/\|54 − \d+\| = \d+/)).toBeInTheDocument();
  expect(screen.getByText(/Gx -?\d+ · Gy -?\d+ → \(\|-?\d+\| \+ \|-?\d+\| \+ 2\) ≫ 2 = \d+/)).toBeInTheDocument();
});

test("the grids show the real per-pixel numbers", () => {
  render(<PixelWalkthrough source={source} blurRadius={1} />);

  // gray(4, 4) = (77·96 + 150·48 + 29·40 + 128) >> 8 = 62 sits one cell right
  // of the center of the grayscale grid.
  const grayGrid = screen.getByLabelText("The same pixels as gray values");
  const values = Array.from(grayGrid.querySelectorAll("span")).map(
    (cell) => cell.textContent,
  );
  expect(values).toHaveLength(25);
  expect(values[12]).toBe("54"); // center of the 5×5
  expect(values[13]).toBe("62");
});

test("picking a different window recomputes every stage", () => {
  render(<PixelWalkthrough source={source} blurRadius={1} />);

  const eyeChip = screen.getByRole("button", { name: "left eye" });
  const backgroundChip = screen.getByRole("button", { name: "background" });
  expect(eyeChip).toHaveAttribute("aria-pressed", "true");
  expect(backgroundChip).toHaveAttribute("aria-pressed", "false");

  fireEvent.click(backgroundChip);

  // The background sample (0.78, 0.3) lands on pixel (6, 2): R 144, G 24, B 40.
  expect(backgroundChip).toHaveAttribute("aria-pressed", "true");
  expect(
    screen.getByText(/A 5×5 window at the background, followed through the whole chain/),
  ).toBeInTheDocument();
  expect(screen.getByText(/R 144 · G 24 · B 40/)).toBeInTheDocument();
});
