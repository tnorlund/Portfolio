import { render } from "@testing-library/react";
import React from "react";
import GithubLogo from "./GithubLogo";

test("renders Github logo SVG", () => {
  const { container } = render(<GithubLogo />);
  expect(container.querySelector("svg")).toBeInTheDocument();
});
