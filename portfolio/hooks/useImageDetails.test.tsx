import { renderHook, waitFor } from '@testing-library/react';
import useImageDetails from './useImageDetails';
import { api } from '../services/api';
import { detectImageFormatSupport } from '../utils/image';

jest.mock('../services/api');
jest.mock('../utils/image');

const mockedApi = api as jest.Mocked<typeof api>;
const mockedDetect = detectImageFormatSupport as jest.MockedFunction<typeof detectImageFormatSupport>;

describe('useImageDetails', () => {
  beforeEach(() => {
    mockedApi.fetchRandomImageDetails.mockResolvedValue({
      image: { image_id: '1' } as any,
      lines: [],
      receipts: [],
    });
    mockedDetect.mockResolvedValue({ supportsAVIF: true, supportsWebP: false });
  });

  test('loads details and format support', async () => {
    const { result } = renderHook(() => useImageDetails('test'));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockedApi.fetchRandomImageDetails).toHaveBeenCalledWith('test');
    expect(result.current.imageDetails?.image.image_id).toBe('1');
    expect(result.current.formatSupport?.supportsAVIF).toBe(true);
  });
});
