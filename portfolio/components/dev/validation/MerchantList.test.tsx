import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import MerchantList from "./MerchantList";
import { MerchantRow } from "./types";

const merchant = (
  name: string,
  mismatch: number,
  matchRate: number,
): MerchantRow => ({
  name,
  receipts: 10,
  match: Math.round(matchRate * 10),
  near: 1,
  mismatch,
  "no-baseline": 0,
  with_bank: 8,
  match_rate: matchRate,
});

const merchants = [
  merchant("Beta Market", 2, 0.7),
  merchant("Alpha Foods", 1, 0.9),
  merchant("Gamma Grocer", 5, 0.4),
];

const renderList = (ref?: React.RefObject<HTMLInputElement | null>) =>
  render(
    <MerchantList
      ref={ref}
      merchants={merchants}
      totals={{ mismatch: 8, near: 3 }}
      receipts={30}
      selected={null}
      statusFilter="failures"
      onSelect={jest.fn()}
      onStatusChange={jest.fn()}
    />,
  );

const visibleMerchantNames = () =>
  screen.getAllByRole("meter").map((meter) => {
    const button = meter.closest("button");
    return within(button as HTMLButtonElement).getByText(/Foods|Market|Grocer/)
      .textContent;
  });

describe("MerchantList", () => {
  it("searches merchants while keeping match-rate meters", () => {
    renderList();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search merchants" }), {
      target: { value: "alpha" },
    });
    expect(screen.getByRole("button", { name: /Alpha Foods/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Gamma Grocer/ })).not.toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "Alpha Foods match rate" })).toHaveAttribute(
      "aria-valuenow",
      "90",
    );
  });

  it("sorts by mismatch count, match rate, or name", () => {
    renderList();
    expect(visibleMerchantNames()).toEqual([
      "Gamma Grocer",
      "Beta Market",
      "Alpha Foods",
    ]);

    fireEvent.change(screen.getByLabelText("Sort merchants"), {
      target: { value: "name" },
    });
    expect(visibleMerchantNames()).toEqual([
      "Alpha Foods",
      "Beta Market",
      "Gamma Grocer",
    ]);

    fireEvent.change(screen.getByLabelText("Sort merchants"), {
      target: { value: "match-rate" },
    });
    expect(visibleMerchantNames()).toEqual([
      "Gamma Grocer",
      "Beta Market",
      "Alpha Foods",
    ]);
  });

  it("forwards the search input ref for the m keyboard shortcut", () => {
    const ref = React.createRef<HTMLInputElement>();
    renderList(ref);
    ref.current?.focus();
    expect(screen.getByRole("searchbox")).toHaveFocus();
  });
});
