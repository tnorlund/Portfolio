import { fireEvent, render, screen } from "@testing-library/react";
import SectionEmbeddingExplorer, { advanceExplorerAutoplay } from ".";
import {
  EXPLORER_ACTS,
  nearestProjectionPoints,
  QUERY_BY_ID,
} from "./sectionData";

jest.mock("react-intersection-observer", () => ({
  useInView: () => ({ ref: jest.fn(), inView: true }),
}));

let reducedMotion = false;

beforeEach(() => {
  jest.spyOn(window, "requestAnimationFrame").mockImplementation(() => 0);
  jest.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: () => ({
      matches: reducedMotion,
      media: "(prefers-reduced-motion: reduce)",
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
    }),
  });
});

afterEach(() => {
  reducedMotion = false;
  jest.restoreAllMocks();
});

test("autoplay math advances and wraps with its remainder", () => {
  expect(advanceExplorerAutoplay(1, 0.2, 1400, 7000, 5)).toEqual({
    activeAct: 1,
    actProgress: 0.4,
  });
  const wrapped = advanceExplorerAutoplay(4, 0.9, 1800, 9000, 5);
  expect(wrapped.activeAct).toBe(0);
  expect(wrapped.actProgress).toBeCloseTo(0.1, 5);
});

test("the explainer opens on ordered receipt rows and exposes five steps", () => {
  render(<SectionEmbeddingExplorer />);
  expect(screen.getByTestId("section-embedding-explorer")).toHaveAttribute(
    "data-mode",
    "autoplay",
  );
  expect(screen.getByTestId("section-act-receipt")).toBeInTheDocument();
  expect(screen.getAllByTestId(/section-act-dot-/)).toHaveLength(
    EXPLORER_ACTS.length,
  );
});

test("manual navigation resolves an act and row choices update neighbor votes", () => {
  render(<SectionEmbeddingExplorer />);
  fireEvent.click(screen.getByTestId("section-act-dot-2"));
  expect(screen.getByTestId("section-act-neighbors")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "VISA •••• 1234" }));
  expect(screen.getByRole("button", { name: "VISA •••• 1234" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByText("85%")).toBeInTheDocument();
});

test("the result act reports the measured held-out comparison", () => {
  render(<SectionEmbeddingExplorer />);
  fireEvent.click(screen.getByTestId("section-act-dot-4"));
  expect(screen.getByText("85.95%")).toBeInTheDocument();
  expect(screen.getByText("90.84%")).toBeInTheDocument();
  expect(screen.getByText("+4.89 pp")).toBeInTheDocument();
});

test("reduced motion renders every step as a resolved static stack", () => {
  reducedMotion = true;
  render(<SectionEmbeddingExplorer />);
  expect(screen.getByTestId("section-embedding-explorer")).toHaveAttribute(
    "data-mode",
    "static",
  );
  expect(screen.getByTestId("section-act-receipt")).toBeInTheDocument();
  expect(screen.getByTestId("section-act-projection")).toBeInTheDocument();
  expect(screen.getByTestId("section-act-neighbors")).toBeInTheDocument();
  expect(screen.getByTestId("section-act-decode")).toBeInTheDocument();
  expect(screen.getByTestId("section-act-result")).toBeInTheDocument();
});

test("the neighborhood contains exactly 15 rows from other merchants", () => {
  const neighbors = nearestProjectionPoints(QUERY_BY_ID.subtotal);
  expect(neighbors).toHaveLength(15);
  expect(neighbors.every((neighbor) => neighbor.merchant !== "Sprouts")).toBe(
    true,
  );
});

