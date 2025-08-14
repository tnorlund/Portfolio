/**
 * Basic test to verify Jest setup is working
 */

// Export statement to make this a module
export {};

describe("Jest Setup", () => {
  it("should be able to run tests", () => {
    expect(true).toBe(true);
  });

  it("should have testing-library/jest-dom matchers available", () => {
    const element = document.createElement("div");
    element.textContent = "Hello World";
    document.body.appendChild(element);

    expect(element).toBeInTheDocument();
    expect(element).toHaveTextContent("Hello World");
  });

  it("should have browser APIs mocked", () => {
    expect(global.IntersectionObserver).toBeDefined();
    expect(global.ResizeObserver).toBeDefined();
    expect(window.matchMedia).toBeDefined();
  });
});
