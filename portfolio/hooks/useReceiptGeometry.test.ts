import { renderHook } from '@testing-library/react';
import useReceiptGeometry from './useReceiptGeometry';

const baseLine = {
  image_id: '1',
  line_id: 1,
  text: 'test',
  bounding_box: { x: 0, y: 0, width: 1, height: 1 },
  top_left: { x: 0, y: 1 },
  top_right: { x: 1, y: 1 },
  bottom_left: { x: 0, y: 0 },
  bottom_right: { x: 1, y: 0 },
  angle_degrees: 0,
  angle_radians: 0,
  confidence: 1,
};

const lines = [baseLine];

describe('useReceiptGeometry', () => {
  test('returns stable value when lines do not change', () => {
    const { result, rerender } = renderHook(
      ({ input }) => useReceiptGeometry(input),
      { initialProps: { input: lines } }
    );

    const first = result.current;
    rerender({ input: lines });
    expect(result.current).toBe(first);
  });

  test('updates when lines change', () => {
    const { result, rerender } = renderHook(
      ({ input }) => useReceiptGeometry(input),
      { initialProps: { input: lines } }
    );

    const first = result.current;
    const newLines = [...lines, { ...baseLine, line_id: 2, bounding_box: { x: 0, y: 1, width: 1, height: 1 }, top_left: { x: 0, y: 2 }, top_right: { x: 1, y: 2 }, bottom_left: { x: 0, y: 1 }, bottom_right: { x: 1, y: 1 } }];
    rerender({ input: newLines });
    expect(result.current).not.toBe(first);
  });
});
