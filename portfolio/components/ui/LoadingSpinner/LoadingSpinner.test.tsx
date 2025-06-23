import { render } from "@testing-library/react";
import React from "react";
import LoadingSpinner from "./LoadingSpinner";
import styles from "./LoadingSpinner.module.css";

describe("LoadingSpinner", () => {
  test("defaults to medium size", () => {
    const { container } = render(<LoadingSpinner />);
    const outer = container.firstChild as HTMLElement;
    expect(outer).toHaveClass(styles.spinner);
    expect(outer).toHaveClass(styles.medium);
  });

  (['small', 'medium', 'large'] as const).forEach(size => {
    test(`applies ${size} class`, () => {
      const { container } = render(<LoadingSpinner size={size} />);
      const outer = container.firstChild as HTMLElement;
      expect(outer).toHaveClass(styles.spinner);
      expect(outer).toHaveClass(styles[size]);
    });
  });
});
