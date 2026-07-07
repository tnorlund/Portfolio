import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import fs from "fs";
import path from "path";
import AugmentationShowcase from ".";

jest.mock("react-intersection-observer", () => ({
  useInView: () => ({
    ref: jest.fn(),
    inView: true,
  }),
}));

const SHOWCASE_DIR = path.join(
  __dirname,
  "../../../../public/synthetic-receipts/showcase",
);

// Serve the real committed labels.json files through the mocked fetch so the
// overlay renders exactly what production would.
beforeEach(() => {
  global.fetch = jest.fn((input: RequestInfo | URL) => {
    const url = String(input);
    const file = url.split("/").pop() ?? "";
    const full = path.join(SHOWCASE_DIR, file);
    if (!fs.existsSync(full)) {
      return Promise.resolve({ ok: false } as Response);
    }
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve(JSON.parse(fs.readFileSync(full, "utf-8"))),
    } as Response);
  }) as jest.Mock;
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe("AugmentationShowcase", () => {
  test("starts on the real base receipt with the counter at zero", async () => {
    render(<AugmentationShowcase />);

    expect(
      screen.getByRole("img", { name: /Real receipt/ }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("generated-counter")).toHaveTextContent(
      "Labeled training examples generated: 0 / 2",
    );
    expect(
      screen.getByRole("button", { name: "Original" }),
    ).toHaveAttribute("aria-pressed", "true");
    // No highlights on the untouched base receipt.
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("base.labels.json"),
      ),
    );
    expect(screen.queryAllByTestId("highlight-box")).toHaveLength(0);
  });

  test("applying an operation swaps the render, shows the caption, and counts it", async () => {
    render(<AugmentationShowcase />);

    fireEvent.click(screen.getByRole("button", { name: "+ LIMES" }));

    expect(
      screen.getByRole("img", { name: /Add line item/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Added LIMES \(PRODUCE\), total 29\.08/),
    ).toBeInTheDocument();
    expect(screen.getByText("$30.58")).toBeInTheDocument();
    expect(screen.getByTestId("generated-counter")).toHaveTextContent(
      "1 / 2",
    );

    // The injected line + recomputed total pulse once labels arrive.
    await waitFor(() =>
      expect(
        screen.getAllByTestId("highlight-box").length,
      ).toBeGreaterThan(0),
    );
    const highlighted = screen
      .getAllByTestId("highlight-box")
      .map((el) => el.getAttribute("data-token"));
    expect(highlighted).toContain("LIMES");
  });

  test("revisiting a variant does not double-count", () => {
    render(<AugmentationShowcase />);

    const add = screen.getByRole("button", { name: "+ LIMES" });
    fireEvent.click(add);
    fireEvent.click(screen.getByRole("button", { name: "Original" }));
    fireEvent.click(add);

    expect(screen.getByTestId("generated-counter")).toHaveTextContent(
      "1 / 2",
    );

    fireEvent.click(screen.getByRole("button", { name: "− SOUR CREAM" }));
    expect(screen.getByTestId("generated-counter")).toHaveTextContent(
      "2 / 2",
    );
  });

  test("each variant discloses which real receipt it was generated from", async () => {
    render(<AugmentationShowcase />);

    fireEvent.click(screen.getByRole("button", { name: "+ LIMES" }));
    await waitFor(() =>
      expect(screen.getByTestId("provenance")).toHaveTextContent("37333eb8"),
    );

    fireEvent.click(screen.getByRole("button", { name: "− SOUR CREAM" }));
    await waitFor(() =>
      expect(screen.getByTestId("provenance")).toHaveTextContent("00ded398"),
    );
  });

  test("reveal labels toggles the ground-truth overlay and legend", async () => {
    render(<AugmentationShowcase />);

    const toggle = screen.getByRole("button", { name: "Reveal labels" });
    fireEvent.click(toggle);

    await waitFor(() =>
      expect(screen.getAllByTestId("label-box").length).toBeGreaterThan(0),
    );
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Merchant")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Hide labels" }));
    expect(screen.queryAllByTestId("label-box")).toHaveLength(0);
  });
});
