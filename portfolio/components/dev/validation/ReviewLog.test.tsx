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
  it("groups per-receipt history and counts resolved receipts", () => {
    render(
      <ReviewLog
        entries={[
          entry(),
          entry({ verdict: "resolved", ts: "2026-07-31T19:00:00.000Z" }),
          entry({ image_id: "image-2", receipt_id: 2, merchant: "Deli" }),
        ]}
        onJump={jest.fn()}
      />,
    );
    expect(screen.getByText("2 receipts")).toBeInTheDocument();
    expect(screen.getByText("1 resolved")).toBeInTheDocument();
    expect(screen.getByText("2 events")).toBeInTheDocument();
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
