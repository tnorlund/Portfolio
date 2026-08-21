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
    expect(screen.getAllByText("Difference", { selector: "figcaption" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Features" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Watershed" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Average color" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Read the paper" })).toHaveAttribute(
      "href",
      "https://doi.org/10.1109/ACSSC.2017.8335175",
    );
    expect(
      screen.getByText(/Paper and engine defaults stay 50\/30\/20/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/homepage pass shown here uses 30\/64\/6/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Corners for markers, edges for the flood" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/A 5×5 window at the left eye, followed through the whole chain/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "left eye" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show Score step" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show Local max step" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show Tiers step" }),
    ).toBeInTheDocument();
  });

  it("steps through the pipeline one stage at a time", () => {
    jest.useFakeTimers();
    try {
      render(<RotoscopePage />);

      // Only the active stage is announced; the dots navigate.
      expect(screen.getAllByText("Difference", { selector: "figcaption" })).toHaveLength(1);
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
      expect(screen.getAllByText("Difference", { selector: "figcaption" })).toHaveLength(1);
    } finally {
      jest.useRealTimers();
    }
  });

  it("illustrates every stage with real stills from the portrait pass", () => {
    render(<RotoscopePage />);

    // The pixel story loads its values from the portrait in the browser; in
    // jsdom only the photograph and its window chips render.
    expect(
      screen.getByAltText(
        "The original portrait; the marked window at the left eye is traced below",
      ),
    ).toHaveAttribute("src", "/rotoscope-portrait.jpg");
    fireEvent.click(screen.getByRole("button", { name: "background" }));
    expect(
      screen.getByAltText(
        "The original portrait; the marked window at the background is traced below",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show Local max step" }));
    expect(
      screen.getByAltText("Shi-Tomasi score field stretched for display"),
    ).toHaveAttribute("src", "/rotoscope-shi-tomasi.webp");
    fireEvent.click(screen.getByRole("button", { name: "Show Tiers step" }));
    expect(
      screen.getByAltText(
        "Focus map: blue periocular ellipse, orange body polygon, gray background",
      ),
    ).toHaveAttribute("src", "/rotoscope-focus.webp");
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
