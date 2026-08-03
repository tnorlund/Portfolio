import { fireEvent, render, screen } from "@testing-library/react";
import ReviewLog from "./ReviewLog";
import { ReviewEntry } from "./types";

const entry = (overrides: Partial<ReviewEntry> = {}): ReviewEntry => ({
  image_id: "image-1",
  receipt_id: 7,
  verdict: "flag",
  note: "items zone is short",
  merchant: "Corner Market",
  status: "mismatch",
  delta: -4.2,
  author: "user",
  ts: "2026-07-31T18:00:00.000Z",
  ...overrides,
});

describe("ReviewLog", () => {
  it("groups per-receipt history and counts the session's output verdicts", () => {
    render(
      <ReviewLog
        entries={[
          entry(),
          entry({ verdict: "golden", ts: "2026-07-31T19:00:00.000Z" }),
          entry({
            image_id: "image-2",
            receipt_id: 2,
            merchant: "Deli",
            verdict: "approve-fix",
          }),
        ]}
        onJump={jest.fn()}
      />,
    );
    expect(screen.getByText("2 receipts")).toBeInTheDocument();
    expect(screen.getByText("1 golden")).toBeInTheDocument();
    expect(screen.getByText("1 to fix")).toBeInTheDocument();
    expect(screen.getByText("2 events")).toBeInTheDocument();
  });

  it("falls back to the reason code when a verdict carried no note", () => {
    render(
      <ReviewLog
        entries={[
          entry({
            verdict: "approve-fix",
            note: "",
            reason: "H-zone-gap-missing-items",
          }),
        ]}
        onJump={jest.fn()}
      />,
    );
    expect(screen.getByText("H-zone-gap-missing-items")).toBeInTheDocument();
  });

  it("jumps to a receipt from a log entry", () => {
    const onJump = jest.fn();
    const review = entry();
    render(<ReviewLog entries={[review]} onJump={onJump} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Jump to Corner Market receipt 7" }),
    );
    expect(onJump).toHaveBeenCalledWith(review);
  });
});
