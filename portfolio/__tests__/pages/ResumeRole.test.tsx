import { render, screen } from "@testing-library/react";
import React from "react";
import { ResumeRole } from "../../pages/resume";

describe("ResumeRole", () => {
  const sampleRole = {
    title: "Engineer",
    business: "ACME Corp",
    location: "NYC",
    dateRange: "2020-2021",
    bullets: ["Did something", "Did another thing"],
  };

  test("renders bullets and date range", () => {
    render(<ResumeRole {...sampleRole} />);
    expect(screen.getByText(sampleRole.dateRange)).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(sampleRole.bullets.length);
    sampleRole.bullets.forEach((text) => {
      expect(screen.getByText(text)).toBeInTheDocument();
    });
  });

  test("renders without business and location", () => {
    const { container } = render(
      <ResumeRole
        title="Engineer"
        dateRange="2020-2021"
        bullets={["Did something"]}
      />,
    );

    expect(container.querySelector("hr")).toBeNull();
    expect(screen.getByText("2020-2021")).toBeInTheDocument();
    expect(screen.getByText("Did something")).toBeInTheDocument();
  });
});
