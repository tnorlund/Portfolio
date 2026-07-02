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
      "Labeled training examples generated: 0 / 3",
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
      "1 / 3",
    );

    // The injected line + recomputed total pulse once labels arrive.
    await waitFor(() =>
      expect(
        screen.getAllByTestId("highlight-box").length,
      ).toBeGreaterThan(0),
    );
    const highlighted = screen
      .getAllByTestId("highlight-box")
      .map((el) => el.getAttribute("title"));
    expect(highlighted.some((t) => t?.includes("LIMES"))).toBe(true);
  });

  test("revisiting a variant does not double-count", () => {
    render(<AugmentationShowcase />);

    const add = screen.getByRole("button", { name: "+ LIMES" });
    fireEvent.click(add);
    fireEvent.click(screen.getByRole("button", { name: "Original" }));
    fireEvent.click(add);

    expect(screen.getByTestId("generated-counter")).toHaveTextContent(
      "1 / 3",
    );

    fireEvent.click(screen.getByRole("button", { name: "− SOUR CREAM" }));
    fireEvent.click(screen.getByRole("button", { name: "− CAGE" }));
    expect(screen.getByTestId("generated-counter")).toHaveTextContent(
      "3 / 3",
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
    expect(screen.getByText("MERCHANT_NAME")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Hide labels" }));
    expect(screen.queryAllByTestId("label-box")).toHaveLength(0);
  });
});
