import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { fetchDigest, postApprove } from "./client";
import DigestPanel from "./DigestPanel";
import { DigestGroup } from "./types";

jest.mock("./client", () => ({
  fetchDigest: jest.fn(),
  postApprove: jest.fn(),
}));

const mockedFetchDigest = jest.mocked(fetchDigest);
const mockedPostApprove = jest.mocked(postApprove);

const group = (overrides: Partial<DigestGroup> = {}): DigestGroup => ({
  group_id: "Smith's::H-zone-gap-missing-items",
  merchant: "Smith's",
  failure_mode: "H-zone-gap-missing-items",
  action: "extend_items_section",
  golden_candidate: false,
  count: 3,
  net_delta: -188.55,
  receipts: [-61.85, -62.85, -63.85].map((delta, index) => ({
    image_id: `image-${index + 1}`,
    receipt_id: 1,
    delta,
    merchant: "Smith's",
    reason: "guarded-extension",
    golden: false,
  })),
  thumbnails: [],
  approved: false,
  frozen: false,
  ...overrides,
});

const digest = (groups: DigestGroup[], frozen: string[] = []) => ({
  pass_id: "pass-1",
  groups,
  passes: ["pass-1"],
  frozen,
  generated_at: null,
  source: "pass-1.jsonl",
});

beforeEach(() => {
  jest.clearAllMocks();
  mockedPostApprove.mockResolvedValue({
    ok: true,
    already: false,
    pass_id: "pass-1",
    group_id: "Smith's::H-zone-gap-missing-items",
    approvals: 1,
    path: "/tmp/approvals/pass-1.json",
  });
});

describe("DigestPanel approve flow", () => {
  it("approves a group once and shows it queued for the writer", async () => {
    mockedFetchDigest.mockResolvedValue(digest([group()]));
    render(<DigestPanel />);

    const button = await screen.findByRole("button", {
      name: /Approve 3 receipt\(s\)/,
    });
    fireEvent.click(button);

    await waitFor(() =>
      expect(mockedPostApprove).toHaveBeenCalledWith(
        "pass-1",
        "Smith's::H-zone-gap-missing-items",
      ),
    );
    expect(
      await screen.findByTestId("approved-Smith's::H-zone-gap-missing-items"),
    ).toHaveTextContent("Approved");
    // The button is replaced, so the same group cannot be approved twice.
    expect(
      screen.queryByRole("button", { name: /Approve 3 receipt\(s\)/ }),
    ).not.toBeInTheDocument();
  });

  it("surfaces a rejected approval instead of marking the group approved", async () => {
    mockedFetchDigest.mockResolvedValue(digest([group()]));
    mockedPostApprove.mockRejectedValue(new Error("class is frozen"));
    render(<DigestPanel />);

    fireEvent.click(
      await screen.findByRole("button", { name: /Approve 3 receipt\(s\)/ }),
    );
    expect(await screen.findByText("class is frozen")).toBeInTheDocument();
    expect(
      screen.queryByTestId("approved-Smith's::H-zone-gap-missing-items"),
    ).not.toBeInTheDocument();
  });

  it("warns that a golden candidate ratchets the CI floors", async () => {
    mockedFetchDigest.mockResolvedValue(
      digest([
        group({
          group_id: "Gelson's::A-total-line-absorbed",
          merchant: "Gelson's",
          failure_mode: "A-total-line-absorbed",
          golden_candidate: true,
          count: 2,
        }),
      ]),
    );
    render(<DigestPanel />);

    const warning = await screen.findByTestId(
      "golden-warning-Gelson's::A-total-line-absorbed",
    );
    expect(warning).toHaveTextContent("ratchets the CI floors");
    expect(
      screen.getByTestId("digest-group-Gelson's::A-total-line-absorbed"),
    ).toHaveAttribute("data-golden", "true");
  });

  it("refuses to approve a frozen class", async () => {
    mockedFetchDigest.mockResolvedValue(
      digest([group({ frozen: true })], ["H-zone-gap-missing-items"]),
    );
    render(<DigestPanel />);

    expect(
      await screen.findByTestId(
        "frozen-notice-Smith's::H-zone-gap-missing-items",
      ),
    ).toHaveTextContent("is frozen");
    expect(
      screen.getByRole("button", { name: /Approve 3 receipt\(s\)/ }),
    ).toBeDisabled();
  });

  it("reports the freeze state up to the shell", async () => {
    mockedFetchDigest.mockResolvedValue(digest([group()], ["J-unknown"]));
    const onFrozen = jest.fn();
    render(<DigestPanel onFrozen={onFrozen} />);
    await waitFor(() => expect(onFrozen).toHaveBeenCalledWith(["J-unknown"]));
  });

  it("explains an empty pass rather than rendering nothing", async () => {
    mockedFetchDigest.mockResolvedValue(digest([]));
    render(<DigestPanel />);
    expect(await screen.findByTestId("digest-empty")).toHaveTextContent(
      "No batch groups in this pass",
    );
  });
});
