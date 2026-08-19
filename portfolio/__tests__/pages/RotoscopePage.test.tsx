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
      screen.getByText(/homepage portrait overrides them to 70\/22\/8/),
    ).toBeInTheDocument();
  });

  it("illustrates every stage with real stills from the portrait pass", () => {
    render(<RotoscopePage />);

    expect(screen.getByAltText("The source portrait")).toHaveAttribute(
      "src",
      "/rotoscope-portrait.jpg",
    );
    expect(
      screen.getByAltText("Blurred grayscale copy of the portrait"),
    ).toHaveAttribute("src", "/rotoscope-blurred.webp");
    expect(
      screen.getByAltText(/markers over a lightened portrait/),
    ).toHaveAttribute("src", "/rotoscope-markers.webp");
    expect(
      screen.getByAltText("Catchment basin outlines traced over the portrait"),
    ).toHaveAttribute("src", "/rotoscope-basins.webp");
    expect(
      screen.getByAltText(
        "The finished rotoscope: every basin filled with its average color",
      ),
    ).toHaveAttribute("src", "/rotoscope-painted.webp");
  });

  it("can replay the watershed animation", () => {
    render(<RotoscopePage />);

    const before = screen.getByRole("img", {
      name: "Markers flooding outward into catchment basins",
    });
    const frames = before.querySelectorAll("img");
    expect(frames).toHaveLength(5);
    expect(frames[0]).toHaveAttribute("src", "/rotoscope-flood-1.webp");
    expect(frames[4]).toHaveAttribute("src", "/rotoscope-painted.webp");

    fireEvent.click(screen.getByRole("button", { name: "Replay the flood" }));
    const after = screen.getByRole("img", {
      name: "Markers flooding outward into catchment basins",
    });

    expect(after).not.toBe(before);
  });
});
