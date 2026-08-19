import { act, fireEvent, render, screen } from "@testing-library/react";
import RotoscopePage from "../../pages/rotoscope";

jest.mock("react-intersection-observer", () => ({
  useInView: () => ({ ref: jest.fn(), inView: true }),
}));

describe("RotoscopePage", () => {
  it("presents the algorithm as a readable four-stage story", () => {
    render(<RotoscopePage />);

    expect(
      screen.getByRole("heading", { name: "How the rotoscope works" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Difference", { selector: "figcaption" })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Features" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Watershed" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Average color" })).toBeInTheDocument();
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

  it("steps through the pipeline one stage at a time", () => {
    jest.useFakeTimers();
    try {
      render(<RotoscopePage />);

      // Only the active stage is announced; the dots navigate.
      expect(screen.getAllByText("Difference", { selector: "figcaption" })).toHaveLength(2);
      expect(
        screen.queryByText("Watershed", { selector: "figcaption" }),
      ).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Watershed" }));
      expect(screen.getByText("Watershed", { selector: "figcaption" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Watershed" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );

      // A manual jump pauses autoplay; it resumes after the idle window and
      // loops from Watershed to Average color and back around to Difference.
      act(() => {
        jest.advanceTimersByTime(10000);
      });
      act(() => {
        jest.advanceTimersByTime(3200);
      });
      expect(
        screen.getByText("Average color", { selector: "figcaption" }),
      ).toBeInTheDocument();
      act(() => {
        jest.advanceTimersByTime(3200);
      });
      expect(screen.getAllByText("Difference", { selector: "figcaption" })).toHaveLength(2);
    } finally {
      jest.useRealTimers();
    }
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
