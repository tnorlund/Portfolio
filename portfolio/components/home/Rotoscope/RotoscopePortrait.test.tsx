import { fireEvent, render, screen } from "@testing-library/react";
import RotoscopePortrait from "./RotoscopePortrait";

test("keeps the real portrait as the immediate accessible image", () => {
  render(<RotoscopePortrait />);
  const image = screen.getByRole("img", { name: "Tyler Norlund smiling outside" });
  expect(image).toHaveAttribute("src", "/rotoscope-portrait.jpg");
  expect(image).toHaveAttribute("loading", "eager");
  expect(screen.getByText("Best-features rotoscope.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Paper" })).toHaveAttribute(
    "href",
    "https://doi.org/10.1109/ACSSC.2017.8335175",
  );
});

test("degrades to the original portrait when workers are unavailable", () => {
  const originalWorker = global.Worker;
  // @ts-expect-error exercise the compatibility path
  global.Worker = undefined;
  render(<RotoscopePortrait />);
  fireEvent.load(screen.getByRole("img"));
  expect(
    screen.getByText("From my 2017 paper."),
  ).toBeInTheDocument();
  global.Worker = originalWorker;
});
