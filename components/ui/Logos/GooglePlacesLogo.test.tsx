import { render } from "@testing-library/react";
import React from "react";
import GooglePlacesLogo from "./GooglePlacesLogo";

test("renders Google Places logo SVG", () => {
  const { container } = render(<GooglePlacesLogo />);
  expect(container.querySelector("svg")).toBeInTheDocument();
});
