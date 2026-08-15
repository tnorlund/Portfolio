import { fireEvent, render, screen } from "@testing-library/react";
import RotoscopePortrait from "./RotoscopePortrait";

test("keeps the catchment basin map as the immediate accessible image", () => {
  render(<RotoscopePortrait />);
  const image = screen.getByRole("img", {
    name: "Catchment basins outlining Tyler Norlund's portrait",
  });
  expect(image).toHaveAttribute("src", "/rotoscope-basins.webp");
  expect(image).toHaveAttribute("loading", "eager");
  expect(screen.queryByRole("slider")).not.toBeInTheDocument();
  expect(screen.getByText("Best-features rotoscope.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Paper" })).toHaveAttribute(
    "href",
    "https://doi.org/10.1109/ACSSC.2017.8335175",
  );
});

test("keeps the basin map when workers are unavailable", () => {
  const originalWorker = global.Worker;
  // @ts-expect-error exercise the compatibility path
  global.Worker = undefined;
  render(<RotoscopePortrait />);
  const hiddenSource = document.querySelector<HTMLImageElement>(
    'img[aria-hidden="true"]',
  );
  expect(hiddenSource).not.toBeNull();
  fireEvent.load(hiddenSource as HTMLImageElement);
  expect(
    screen.getByText("From my 2017 paper."),
  ).toBeInTheDocument();
  global.Worker = originalWorker;
});
