import { render, screen } from "@testing-library/react";
import EmailPage from "../../pages/email";

// react-intersection-observer's useInView returns a tuple that also carries
// named fields; mirror both shapes so FigureBoundary ({ ref, inView }) and
// useOptimizedInView ([ref, inView]) both resolve as "in view".
jest.mock("react-intersection-observer", () => ({
  useInView: () => {
    const ref = jest.fn();
    return Object.assign([ref, true, undefined], { ref, inView: true });
  },
}));

describe("EmailPage", () => {
  it("tells the email-receipt story in the receipt-page voice", () => {
    render(<EmailPage />);

    for (const name of [
      "Introduction",
      "The Job Bot Gets One Code",
      "The Receipt Reader Gets Parsed Rows",
      "Thirteen Years of Email",
      "Getting the Mail to AWS",
      "The Read Replica",
      "What I Deleted",
      "What Else Should Agents See?",
      "Turning It Off",
      "The Boring Details",
    ]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    // The audit table names the one add and the three deletes.
    expect(screen.getByText("starbucks.com")).toBeInTheDocument();
    expect(screen.getByText("github.com")).toBeInTheDocument();

    expect(screen.getByText(/162,333 messages/)).toBeInTheDocument();
    expect(screen.getByText(/3,845 unique/)).toBeInTheDocument();

    expect(screen.getByRole("link", { name: "receipt page" })).toHaveAttribute(
      "href",
      "/receipt",
    );
    expect(screen.getByRole("link", { name: "GitHub" })).toHaveAttribute(
      "href",
      "https://github.com/tnorlund/Portfolio",
    );
  });

  it("lazily mounts every figure behind a boundary", () => {
    const { container } = render(<EmailPage />);
    const names = [...container.querySelectorAll("[data-figure-boundary]")].map(
      (el) => el.getAttribute("data-figure-boundary"),
    );
    expect(names).toEqual([
      "grok-bot-logo",
      "email-forwarding-rules",
      "email-code-diagram",
      "email-funnel",
      "email-sender-census",
      "email-coverage",
      "aws-logo",
      "email-inbox-diagram",
      "email-replica-diagram",
      "pulumi-logo",
    ]);
  });
});
