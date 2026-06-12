import { renderHook, waitFor } from '@testing-library/react';
import useImageDetails from './useImageDetails';
import { api } from '../services/api';
import { detectImageFormatSupport } from '../utils/image';
import { createQueryWrapper } from '../test-utils/queryWrapper';

jest.mock('../services/api');
jest.mock('../utils/image');

const mockedApi = api as jest.Mocked<typeof api>;
const mockedDetect = detectImageFormatSupport as jest.MockedFunction<typeof detectImageFormatSupport>;

describe('useImageDetails', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedApi.fetchRandomImageDetails.mockResolvedValue({
      image: { image_id: '1' } as any,
      lines: [],
      receipts: [],
    });
    mockedDetect.mockResolvedValue({ supportsAVIF: true, supportsWebP: false });
  });

  test('loads details and format support', async () => {
    const { Wrapper } = createQueryWrapper();
    const { result } = renderHook(() => useImageDetails('test'), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockedApi.fetchRandomImageDetails).toHaveBeenCalledWith('test');
    expect(result.current.imageDetails?.image.image_id).toBe('1');
    expect(result.current.formatSupport?.supportsAVIF).toBe(true);
  });

  test('deduplicates concurrent mounts with the same image type', async () => {
    const { Wrapper } = createQueryWrapper();
    const first = renderHook(() => useImageDetails('test'), {
      wrapper: Wrapper,
    });
    const second = renderHook(() => useImageDetails('test'), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    await waitFor(() => expect(second.result.current.loading).toBe(false));
    expect(mockedApi.fetchRandomImageDetails).toHaveBeenCalledTimes(1);
  });
});
