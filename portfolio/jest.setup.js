import "@testing-library/jest-dom";

// Mock IntersectionObserver
global.IntersectionObserver = class IntersectionObserver {
  constructor() {}
  disconnect() {}
  observe() {}
  unobserve() {}
};

// Mock ResizeObserver
global.ResizeObserver = class ResizeObserver {
  constructor() {}
  disconnect() {}
  observe() {}
  unobserve() {}
};

// Mock matchMedia
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: jest.fn().mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: jest.fn(), // deprecated
    removeListener: jest.fn(), // deprecated
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  })),
});

// Mock canvas for image format detection tests
HTMLCanvasElement.prototype.getContext = jest.fn(() => ({
  fillRect: jest.fn(),
  clearRect: jest.fn(),
  getImageData: jest.fn(() => ({ data: new Array(4) })),
  putImageData: jest.fn(),
  createImageData: jest.fn(() => []),
  setTransform: jest.fn(),
  drawImage: jest.fn(),
  save: jest.fn(),
  fillText: jest.fn(),
  restore: jest.fn(),
  beginPath: jest.fn(),
  moveTo: jest.fn(),
  lineTo: jest.fn(),
  closePath: jest.fn(),
  stroke: jest.fn(),
  translate: jest.fn(),
  scale: jest.fn(),
  rotate: jest.fn(),
  arc: jest.fn(),
  fill: jest.fn(),
  measureText: jest.fn(() => ({ width: 0 })),
  transform: jest.fn(),
  rect: jest.fn(),
  clip: jest.fn(),
}));

HTMLCanvasElement.prototype.toDataURL = jest.fn(
  () => "data:image/png;base64,test"
);

// Polyfill performance API for tests
if (typeof global.performance === 'undefined') {
  global.performance = {
    now: () => Date.now(),
    mark: jest.fn(),
    measure: jest.fn(),
    clearMarks: jest.fn(),
    clearMeasures: jest.fn(),
    getEntriesByName: jest.fn(() => [{ duration: 10 }]),
    getEntriesByType: jest.fn((type) => {
      // Mock modern PerformanceNavigationTiming API
      if (type === 'navigation') {
        return [{
          startTime: 0,
          fetchStart: 10,
          responseStart: 100,
          loadEventEnd: 200,
          domContentLoadedEventEnd: 180,
        }];
      }
      return [];
    }),
    // Keep deprecated timing for backward compatibility in tests
    timing: {
      navigationStart: Date.now(),
      loadEventEnd: Date.now() + 100,
    },
  };
} else {
  // Add missing methods if performance exists but methods don't
  if (!global.performance.mark) {
    global.performance.mark = jest.fn();
  }
  if (!global.performance.measure) {
    global.performance.measure = jest.fn();
  }
  if (!global.performance.clearMarks) {
    global.performance.clearMarks = jest.fn();
  }
  if (!global.performance.clearMeasures) {
    global.performance.clearMeasures = jest.fn();
  }
  if (!global.performance.getEntriesByName) {
    global.performance.getEntriesByName = jest.fn(() => [{ duration: 10 }]);
  }
  if (!global.performance.getEntriesByType) {
    global.performance.getEntriesByType = jest.fn((type) => {
      // Mock modern PerformanceNavigationTiming API
      if (type === 'navigation') {
        return [{
          startTime: 0,
          fetchStart: 10,
          responseStart: 100,
          loadEventEnd: 200,
          domContentLoadedEventEnd: 180,
        }];
      }
      return [];
    });
  }
}
