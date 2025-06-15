import { render, screen, act } from '@testing-library/react';
import AnimatedInView from './AnimatedInView';
import React from 'react';

jest.mock('../../hooks/useOptimizedInView', () => () => [React.createRef(), true]);

describe('AnimatedInView', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  test('replaces content after delay', () => {
    render(
      <AnimatedInView replacement={<span>after</span>} replaceAfterMs={500}>
        before
      </AnimatedInView>
    );
    expect(screen.getByText('before')).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(500);
    });
    expect(screen.getByText('after')).toBeInTheDocument();
  });
});
