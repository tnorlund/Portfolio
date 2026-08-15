import { fireEvent, render, screen, within } from "@testing-library/react";
import RotoscopeLab from "./RotoscopeLab";

test("renders an accessible unlinked lab with real distribution controls", () => {
  render(<RotoscopeLab />);
  expect(screen.getByRole("heading", { name: "Rotoscope Lab" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Back to portfolio/ })).toHaveAttribute(
    "href",
    "/",
  );
  expect(screen.getByRole("group", { name: "Strategy" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Radial" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByRole("group", { name: "Algorithm" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Fractal" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByRole("checkbox", { name: "Show seeds" })).toBeChecked();
  expect(screen.getByRole("img", { name: "Interactive rotoscope result" })).toBeInTheDocument();
});

test("disables no-op controls and reveals the hybrid blend only when relevant", () => {
  render(<RotoscopeLab />);
  fireEvent.click(
    within(screen.getByRole("group", { name: "Strategy" })).getByRole("button", {
      name: "Best features",
    }),
  );
  expect(screen.getByRole("slider", { name: "Origin X" })).toBeDisabled();
  expect(document.querySelector("canvas[data-crosshair]"))?.toHaveAttribute(
    "data-crosshair",
    "hidden",
  );
  expect(screen.getByRole("slider", { name: "Feature scale" })).toBeEnabled();
  expect(screen.queryByRole("slider", { name: "Radial blend" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Hybrid" }));
  expect(screen.getByRole("slider", { name: "Origin X" })).toBeEnabled();
  expect(screen.getByRole("slider", { name: "Radial blend" })).toBeInTheDocument();
  expect(document.querySelector("canvas[data-crosshair]"))?.toHaveAttribute(
    "data-crosshair",
    "visible",
  );

  fireEvent.click(screen.getByRole("button", { name: "White" }));
  expect(screen.getByRole("slider", { name: "Strength" })).toBeEnabled();
  expect(screen.getByRole("slider", { name: "Scale" })).toBeDisabled();
  expect(screen.getByRole("spinbutton", { name: "Seed" })).toBeEnabled();

  fireEvent.click(screen.getByRole("button", { name: "None" }));
  expect(screen.getByRole("slider", { name: "Strength" })).toBeDisabled();
  expect(screen.getByRole("slider", { name: "Scale" })).toBeDisabled();
  expect(screen.getByRole("spinbutton", { name: "Seed" })).toBeDisabled();
});

test("moves the radial origin by clicking the preview and reset restores it", () => {
  render(<RotoscopeLab />);
  const preview = screen.getByRole("img", { name: "Interactive rotoscope result" });
  jest.spyOn(preview, "getBoundingClientRect").mockReturnValue({
    left: 10,
    top: 20,
    width: 400,
    height: 300,
    right: 410,
    bottom: 320,
    x: 10,
    y: 20,
    toJSON: () => ({}),
  });
  fireEvent.click(preview, { clientX: 310, clientY: 95 });
  expect(screen.getByRole("slider", { name: "Origin X" })).toHaveValue("0.75");
  expect(screen.getByRole("slider", { name: "Origin Y" })).toHaveValue("0.25");

  fireEvent.click(screen.getByRole("button", { name: "Reset" }));
  expect(screen.getByRole("slider", { name: "Origin X" })).toHaveValue("0.4");
  expect(screen.getByRole("slider", { name: "Origin Y" })).toHaveValue("0.56");
});
