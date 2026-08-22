import { fireEvent, render, screen } from "@testing-library/react";
import RgbTracer, { samplePoint, TRACER_LOOP } from "./RgbTracer";

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

// jsdom has no SVG path geometry, so the tracer parks at the loop's start
// (37, 50 in the 100×75 box), which is pixel (3, 5) of this 9×9 buffer:
// R 72, G 60, B 40 → gray (77·72 + 150·60 + 29·40 + 128) >> 8 = 61.
const source = {
  width: 9,
  height: 9,
  rgba: rgba(9, 9, (x, y) => [x * 24, y * 12, 40, 255]),
};

test("maps loop coordinates onto the pixel grid", () => {
  expect(samplePoint(source, { x: 0, y: 0 })).toEqual({
    x: 0,
    y: 0,
    red: 0,
    green: 0,
    blue: 40,
  });
  expect(samplePoint(source, { x: 100, y: 75 })).toMatchObject({ x: 8, y: 8 });
  expect(samplePoint(source, { x: 37, y: 50 })).toEqual({
    x: 3,
    y: 5,
    red: 72,
    green: 60,
    blue: 40,
  });
});

test("reads the pixel under the tracer as three alpha circles", () => {
  const { container } = render(<RgbTracer source={source} />);

  expect(container.querySelector("path")).toHaveAttribute("d", TRACER_LOOP);
  expect(screen.getByAltText("The original portrait in color")).toBeInTheDocument();
  expect(screen.getByLabelText("R 72")).toBeInTheDocument();
  expect(screen.getByLabelText("G 60")).toBeInTheDocument();
  expect(screen.getByLabelText("B 40")).toBeInTheDocument();
  expect(screen.getByText(/pixel 3, 5 · three numbers, each 0 to 255/)).toBeInTheDocument();

  const red = screen.getByLabelText("R 72").querySelector("span");
  expect(red).toHaveStyle({ background: `rgba(229, 57, 53, ${72 / 255})` });
});

test("the switch collapses the readout to one black-and-white circle", () => {
  render(<RgbTracer source={source} />);

  const toggle = screen.getByRole("switch", { name: "Black and white" });
  expect(toggle).toHaveAttribute("aria-checked", "false");

  fireEvent.click(toggle);

  expect(toggle).toHaveAttribute("aria-checked", "true");
  expect(screen.getByAltText("The original portrait in black and white")).toBeInTheDocument();
  expect(screen.getByLabelText("Gray 61")).toBeInTheDocument();
  expect(screen.queryByLabelText("R 72")).not.toBeInTheDocument();
  expect(
    screen.getByText(/\(77·72 \+ 150·60 \+ 29·40 \+ 128\) ≫ 8 = 61/),
  ).toBeInTheDocument();

  fireEvent.click(toggle);
  expect(screen.getByLabelText("R 72")).toBeInTheDocument();
});
