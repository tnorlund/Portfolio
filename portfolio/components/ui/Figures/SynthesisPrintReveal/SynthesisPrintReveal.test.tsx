import { act, fireEvent, render, screen } from "@testing-library/react";
import fs from "fs";
import path from "path";
import SynthesisPrintReveal from ".";

jest.mock("react-intersection-observer", () => ({
  useInView: () => ({
    ref: jest.fn(),
    inView: true,
  }),
}));

const LABELS_PATH = path.join(
  __dirname,
  "../../../../public/synthetic-receipts/showcase/generated.labels.json",
);

beforeEach(() => {
  jest.useFakeTimers();
  global.fetch = jest.fn(() =>
    Promise.resolve({
      ok: true,
      json: () =>
        Promise.resolve(JSON.parse(fs.readFileSync(LABELS_PATH, "utf-8"))),
    } as Response),
  ) as jest.Mock;
});

afterEach(() => {
  jest.runOnlyPendingTimers();
  jest.useRealTimers();
  jest.restoreAllMocks();
});

const flushLabels = async () => {
  // resolve the labels fetch promise chain
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};

describe("SynthesisPrintReveal", () => {
  test("walks printing -> zooming -> labeled and then draws the labels", async () => {
    render(<SynthesisPrintReveal />);
    await flushLabels();

    expect(screen.getByTestId("phase-caption")).toHaveTextContent(
      /printed line by line/i,
    );
    expect(screen.queryAllByTestId("reveal-label-box")).toHaveLength(0);

    // print (7000) + settle (600) -> zooming
    act(() => {
      jest.advanceTimersByTime(7600);
    });
    expect(screen.getByTestId("phase-caption")).toHaveTextContent(
      /composed from the merchant/i,
    );

    // + zoom (1400) -> labeled, ground-truth boxes sweep in
    act(() => {
      jest.advanceTimersByTime(1400);
    });
    expect(screen.getByTestId("phase-caption")).toHaveTextContent(
      /ground truth comes for free/i,
    );
    expect(
      screen.getAllByTestId("reveal-label-box").length,
    ).toBeGreaterThan(10);
    expect(screen.getByText("Merchant")).toBeInTheDocument();
  });

  test("labels use the margin-aware render transform", async () => {
    render(<SynthesisPrintReveal />);
    await flushLabels();
    act(() => {
      jest.advanceTimersByTime(9000);
    });

    // With render {width:760,height:1140,margin:10} no box may start at
    // exactly 0% — the margin insets everything.
    const boxes = screen.getAllByTestId("reveal-label-box");
    boxes.forEach((box) => {
      expect(Number(box.getAttribute("x"))).toBeGreaterThan(0);
      expect(Number(box.getAttribute("y"))).toBeGreaterThan(0);
    });
  });

  test("replay restarts the print", async () => {
    render(<SynthesisPrintReveal />);
    await flushLabels();
    act(() => {
      jest.advanceTimersByTime(9000);
    });

    fireEvent.click(screen.getByRole("button", { name: "Print it again" }));
    act(() => {
      jest.advanceTimersByTime(20); // rAF tick
    });
    expect(screen.getByTestId("phase-caption")).toHaveTextContent(
      /printed line by line/i,
    );
    expect(screen.queryAllByTestId("reveal-label-box")).toHaveLength(0);
  });
});
