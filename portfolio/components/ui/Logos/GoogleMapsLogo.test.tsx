import { render } from "@testing-library/react";
import React from "react";
import GoogleMapsLogo from "./GoogleMapsLogo";

test("renders Google Maps logo SVG", () => {
  const { container } = render(<GoogleMapsLogo />);
  expect(container.querySelector("svg")).toBeInTheDocument();
});

