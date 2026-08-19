import { render, screen } from "@testing-library/react";

jest.mock("../../components/home/Rotoscope/workerClient", () => ({
  RotoscopeWorkerClient: jest.fn().mockImplementation(() => ({
    available: () => true,
    render: jest.fn(),
    dispose: jest.fn(),
  })),
}));

import Home from "../../pages/index";

describe("Home", () => {
  it("keeps the full-viewport portrait and links Rotoscope to the explainer", () => {
    render(<Home />);

    expect(screen.queryByText("Best-features rotoscope.")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Paper" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /rotoscope-lab/i })).not.toBeInTheDocument();

    const rotoscope = screen.getByRole("link", { name: "Rotoscope" });
    expect(rotoscope).toHaveAttribute("href", "/rotoscope");
    expect(screen.getByRole("button", { name: "Rotoscope" })).toBeInTheDocument();

    expect(screen.getByRole("link", { name: "Résumé" })).toHaveAttribute(
      "href",
      "/resume",
    );
    expect(screen.getByRole("button", { name: "Résumé" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Receipt" })).toHaveAttribute(
      "href",
      "/receipt",
    );
    expect(screen.getByRole("button", { name: "Receipt" })).toBeInTheDocument();
  });
});
