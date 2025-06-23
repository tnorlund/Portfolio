import { render } from "@testing-library/react";
import React from "react";
import PulumiLogo from "./PulumiLogo";

test("renders Pulumi logo SVG", () => {
  const { container } = render(<PulumiLogo />);
  expect(container.querySelector("svg")).toBeInTheDocument();
});
