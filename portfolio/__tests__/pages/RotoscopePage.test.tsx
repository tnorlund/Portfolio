import { fireEvent, render, screen } from "@testing-library/react";
import RotoscopePage from "../../pages/rotoscope";

describe("RotoscopePage", () => {
  it("presents the algorithm as a readable four-stage story", () => {
    render(<RotoscopePage />);

    expect(
      screen.getByRole("heading", { name: "How the rotoscope works" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Difference", { selector: "figcaption" })).toHaveLength(2);
    expect(screen.getByText("Features", { selector: "figcaption" })).toBeInTheDocument();
    expect(screen.getByText("Watershed", { selector: "figcaption" })).toBeInTheDocument();
    expect(screen.getByText("Average color", { selector: "figcaption" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Read the paper" })).toHaveAttribute(
      "href",
      "https://doi.org/10.1109/ACSSC.2017.8335175",
    );
    expect(
      screen.getByText(/50\/30\/20 shares are the paper and engine defaults/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/homepage portrait overrides them to 55\/30\/15/),
    ).toBeInTheDocument();
  });

  it("can replay the watershed animation", () => {
    render(<RotoscopePage />);

    const before = screen.getByRole("img", {
      name: "Markers flooding outward into catchment basins",
    });
    fireEvent.click(screen.getByRole("button", { name: "Replay the flood" }));
    const after = screen.getByRole("img", {
      name: "Markers flooding outward into catchment basins",
    });

    expect(after).not.toBe(before);
  });
});
