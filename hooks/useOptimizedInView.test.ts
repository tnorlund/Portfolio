import { renderHook } from '@testing-library/react';
import { useInView } from 'react-intersection-observer';
import useOptimizedInView from './useOptimizedInView';

jest.mock('react-intersection-observer', () => ({
  __esModule: true,
  useInView: jest.fn(),
}));

const mockedUseInView = useInView as jest.MockedFunction<typeof useInView>;

const ref = { current: null } as React.RefObject<Element>;
const mockedReturn: ReturnType<typeof useInView> = [ref as any, true, undefined] as any;

describe('useOptimizedInView', () => {
  beforeEach(() => {
    mockedUseInView.mockReturnValue(mockedReturn);
    jest.clearAllMocks();
  });

  test('forwards default options', () => {
    const { result } = renderHook(() => useOptimizedInView());
    expect(result.current).toBe(mockedReturn);
    expect(mockedUseInView).toHaveBeenCalledWith({
      threshold: 0.3,
      triggerOnce: true,
      rootMargin: '100px',
      skip: false,
      fallbackInView: true,
    });
  });

  test('custom options override defaults', () => {
    const options = {
      threshold: 0.5,
      triggerOnce: false,
      rootMargin: '50px',
      skip: true,
    } as const;

    const { result } = renderHook(() => useOptimizedInView(options));
    expect(result.current).toBe(mockedReturn);
    expect(mockedUseInView).toHaveBeenCalledWith({
      ...options,
      fallbackInView: true,
    });
  });
});
