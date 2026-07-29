import { fireEvent, render, screen, within } from "@testing-library/react";
import SectionEmbeddingExplorer, { advanceExplorerAutoplay } from ".";
import { EXPLORER_ACTS } from "./sectionData";

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
  expect(advanceExplorerAutoplay(1, 0.2, 1360, 6800, 5)).toEqual({
    activeAct: 1,
    actProgress: 0.4,
  });
  const wrapped = advanceExplorerAutoplay(4, 0.9, 1640, 8200, 5);
  expect(wrapped.activeAct).toBe(0);
  expect(wrapped.actProgress).toBeCloseTo(0.1, 5);
});

test("opens on unassigned Apple OCR rows and exposes five steps", () => {
  render(<SectionEmbeddingExplorer />);
  expect(screen.getByTestId("section-embedding-explorer")).toHaveAttribute(
    "data-mode",
    "autoplay",
  );
  expect(screen.getByTestId("section-act-ocr")).toBeInTheDocument();
  expect(screen.getByTestId("section-row-subtotal")).not.toHaveAttribute(
    "data-section",
  );
  expect(screen.getByText(/LayoutLM labels words separately/)).toBeInTheDocument();
  expect(screen.getAllByTestId(/section-act-dot-/)).toHaveLength(
    EXPLORER_ACTS.length,
  );
});

test.each([
  [0, "section-act-ocr", undefined, undefined],
  [1, "section-act-baseline", "ITEMS", "SUMMARY"],
  [2, "section-act-neighbors", "ITEMS", "SUMMARY"],
  [3, "section-act-corrected", "SUMMARY", "PAYMENT"],
  [4, "section-act-final", "SUMMARY", "PAYMENT"],
] as const)(
  "manual step %i renders its resolved receipt assignments",
  (index, actTestId, subtotalSection, visaSection) => {
    render(<SectionEmbeddingExplorer />);
    fireEvent.click(screen.getByTestId(`section-act-dot-${index}`));
    const act = screen.getByTestId(actTestId);
    expect(act).toBeInTheDocument();
    const subtotal = within(act).getByTestId("section-row-subtotal");
    const visa = within(act).getByTestId("section-row-visa");
    if (subtotalSection) {
      expect(subtotal).toHaveAttribute("data-section", subtotalSection);
      expect(visa).toHaveAttribute("data-section", visaSection);
    } else {
      expect(subtotal).not.toHaveAttribute("data-section");
      expect(visa).not.toHaveAttribute("data-section");
    }
  },
);

test("neighbor step shows the labeled stack and row search provenance", () => {
  render(<SectionEmbeddingExplorer />);
  fireEvent.click(screen.getByTestId("section-act-dot-2"));
  const act = screen.getByTestId("section-act-neighbors");
  expect(within(act).getAllByTestId(/section-reference-receipt-/)).toHaveLength(4);
  expect(within(act).getByText(/OpenAI creates row embeddings/)).toBeInTheDocument();
  expect(within(act).getByText(/2-D map is schematic, not literal/)).toBeInTheDocument();
  expect(document.querySelectorAll('[data-neighbor="true"]').length).toBeGreaterThan(4);
});

test("corrected and final steps mark both rows that changed", () => {
  render(<SectionEmbeddingExplorer />);
  fireEvent.click(screen.getByTestId("section-act-dot-3"));
  const correctedAct = screen.getByTestId("section-act-corrected");
  expect(within(correctedAct).getByTestId("section-row-subtotal")).toHaveAttribute(
    "data-changed",
    "true",
  );
  expect(within(correctedAct).getByTestId("section-row-visa")).toHaveAttribute(
    "data-changed",
    "true",
  );
  expect(within(correctedAct).getByText(/geometry ×0.0/)).toBeInTheDocument();

  fireEvent.click(screen.getByTestId("section-act-dot-4"));
  expect(
    within(screen.getByTestId("section-act-final")).getByText(
      /held-out row agreement \+4.89 pp/,
    ),
  ).toBeInTheDocument();
});

test("arrow, Home, and End keys select and focus resolved steps", () => {
  render(<SectionEmbeddingExplorer />);
  const first = screen.getByTestId("section-act-dot-0");
  first.focus();
  fireEvent.keyDown(first, { key: "ArrowRight" });
  expect(screen.getByTestId("section-act-dot-1")).toHaveFocus();
  expect(screen.getByTestId("section-act-baseline")).toBeInTheDocument();

  fireEvent.keyDown(screen.getByTestId("section-act-dot-1"), { key: "End" });
  expect(screen.getByTestId("section-act-dot-4")).toHaveFocus();
  expect(screen.getByTestId("section-act-final")).toBeInTheDocument();

  fireEvent.keyDown(screen.getByTestId("section-act-dot-4"), { key: "Home" });
  expect(screen.getByTestId("section-act-dot-0")).toHaveFocus();
});

test("reduced motion renders every step as a resolved static stack", () => {
  reducedMotion = true;
  render(<SectionEmbeddingExplorer />);
  expect(screen.getByTestId("section-embedding-explorer")).toHaveAttribute(
    "data-mode",
    "static",
  );
  for (const act of EXPLORER_ACTS) {
    expect(screen.getByTestId(`section-act-${act.id}`)).toBeInTheDocument();
  }
});
