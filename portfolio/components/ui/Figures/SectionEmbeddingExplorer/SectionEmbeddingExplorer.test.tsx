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

test("opens on unassigned real Apple OCR rows and exposes five steps", () => {
  render(<SectionEmbeddingExplorer />);
  expect(screen.getByTestId("section-embedding-explorer")).toHaveAttribute(
    "data-mode",
    "autoplay",
  );
  expect(screen.getByTestId("section-act-ocr")).toBeInTheDocument();
  expect(screen.getByTestId("section-row-row-11")).not.toHaveAttribute(
    "data-section",
  );
  expect(screen.getByTestId("section-current-receipt")).toHaveAttribute(
    "data-source-id",
    "d47b0f01 · R1",
  );
  expect(screen.getAllByText("Salt & Straw").length).toBeGreaterThan(0);
  expect(screen.getByText(/LayoutLM labels words separately/)).toBeInTheDocument();
  expect(screen.getAllByTestId(/section-act-dot-/)).toHaveLength(
    EXPLORER_ACTS.length,
  );
});

test("renders the real receipt image and positions row overlays from stored geometry", () => {
  render(<SectionEmbeddingExplorer />);
  const image = screen.getByTestId("section-current-image");
  expect(image).toHaveAttribute(
    "src",
    expect.stringContaining(
      "/assets/d47b0f01-859d-499b-a9b0-4feb312b4d27/1.webp",
    ),
  );
  expect(image).toHaveAttribute("width", "425");
  expect(image).toHaveAttribute("height", "884");

  const subtotalBox = screen.getByTestId("section-row-row-11");
  expect(parseFloat(subtotalBox.style.left)).toBeCloseTo(8.4592, 3);
  expect(parseFloat(subtotalBox.style.top)).toBeCloseTo(50.2907, 3);
  expect(parseFloat(subtotalBox.style.width)).toBeCloseTo(17.5227, 3);
  expect(parseFloat(subtotalBox.style.height)).toBeCloseTo(2.3256, 3);
  expect(subtotalBox).toHaveAccessibleName(/OCR row 11: Subtotal/);
});

test.each([
  [0, "section-act-ocr", undefined, undefined],
  [1, "section-act-baseline", "TRANSACTION_INFO", "TRANSACTION_INFO"],
  [2, "section-act-neighbors", "TRANSACTION_INFO", "TRANSACTION_INFO"],
  [3, "section-act-corrected", "SUMMARY", "PAYMENT"],
  [4, "section-act-final", "SUMMARY", "PAYMENT"],
] as const)(
  "manual step %i renders its resolved receipt assignments",
  (index, actTestId, subtotalSection, paymentSection) => {
    render(<SectionEmbeddingExplorer />);
    fireEvent.click(screen.getByTestId(`section-act-dot-${index}`));
    const act = screen.getByTestId(actTestId);
    expect(act).toBeInTheDocument();
    const subtotal = within(act).getByTestId("section-row-row-11");
    const paymentTime = within(act).getByTestId("section-row-row-34");
    if (subtotalSection) {
      expect(subtotal).toHaveAttribute("data-section", subtotalSection);
      expect(paymentTime).toHaveAttribute("data-section", paymentSection);
    } else {
      expect(subtotal).not.toHaveAttribute("data-section");
      expect(paymentTime).not.toHaveAttribute("data-section");
    }
  },
);

test("neighbor step shows real labeled receipts and measured search provenance", () => {
  render(<SectionEmbeddingExplorer />);
  fireEvent.click(screen.getByTestId("section-act-dot-2"));
  const act = screen.getByTestId("section-act-neighbors");
  expect(within(act).getAllByTestId(/section-reference-receipt-/)).toHaveLength(4);
  expect(within(act).getAllByTestId(/section-reference-image-/)).toHaveLength(4);
  expect(within(act).getByText("Mouthful Eatery")).toBeInTheDocument();
  expect(within(act).getByText("Aloha Sunrise Cafe")).toBeInTheDocument();
  expect(within(act).getAllByText("0.901").length).toBeGreaterThan(0);
  expect(within(act).getByText(/OpenAI created the row embeddings/)).toBeInTheDocument();
  expect(within(act).getByText(/2-D map is schematic/)).toBeInTheDocument();
  expect(document.querySelectorAll('[data-neighbor="true"]').length).toBeGreaterThan(4);
});

test("corrected and final steps keep six fixes and one unresolved row honest", () => {
  render(<SectionEmbeddingExplorer />);
  fireEvent.click(screen.getByTestId("section-act-dot-3"));
  const correctedAct = screen.getByTestId("section-act-corrected");
  expect(within(correctedAct).getByTestId("section-row-row-11")).toHaveAttribute(
    "data-corrected",
    "true",
  );
  expect(within(correctedAct).getByTestId("section-row-row-34")).toHaveAttribute(
    "data-corrected",
    "true",
  );
  expect(within(correctedAct).getByTestId("section-row-row-31")).toHaveAttribute(
    "data-unresolved",
    "true",
  );
  expect(correctedAct.querySelectorAll('[data-corrected="true"]')).toHaveLength(6);
  expect(correctedAct.querySelectorAll('[data-unresolved="true"]')).toHaveLength(1);
  expect(within(correctedAct).getByText(/geometry ×0.0/)).toBeInTheDocument();

  fireEvent.click(screen.getByTestId("section-act-dot-4"));
  const finalAct = screen.getByTestId("section-act-final");
  expect(
    within(finalAct).getByTestId("section-current-receipt").querySelectorAll(
      '[data-testid="section-band"]',
    ),
  ).toHaveLength(4);
  expect(within(finalAct).getByTestId("section-row-row-31")).toHaveAttribute(
    "data-section",
    "PAYMENT",
  );
  expect(
    within(finalAct).getByText(
      /held-out agreement \+4.89 pp/,
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
