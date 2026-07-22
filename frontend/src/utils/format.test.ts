import { describe, expect, it } from 'vitest';
import { clampToRange, computeInitialBudget, formatBudget, pickBudgetStep } from './format';

describe('formatBudget', () => {
  it('formats whole rubles with ru-RU thousands separators (U+00A0)', () => {
    expect(formatBudget(150_000)).toBe('150 000 ₽');
  });

  it('rounds fractional amounts', () => {
    expect(formatBudget(999.6)).toBe('1 000 ₽');
  });
});

describe('pickBudgetStep', () => {
  it('picks a fine step for small ranges', () => {
    expect(pickBudgetStep(0, 500)).toBe(10);
  });

  it('picks a coarser step for large ranges', () => {
    expect(pickBudgetStep(0, 200_000)).toBe(1_000);
  });

  it('never returns a non-positive step for a zero-width range', () => {
    expect(pickBudgetStep(500, 500)).toBe(1);
  });
});

describe('clampToRange', () => {
  it('leaves an in-range value untouched', () => {
    expect(clampToRange(105, 101, 109)).toBe(105);
  });

  it('clamps a value above max down to max', () => {
    expect(clampToRange(110, 101, 109)).toBe(109);
  });

  it('clamps a value below min up to min', () => {
    expect(clampToRange(50, 101, 109)).toBe(101);
  });
});

describe('computeInitialBudget', () => {
  it('never overshoots max when the rounded midpoint would exceed it (min=101, max=109, step=10)', () => {
    // Midpoint is 105; Math.round(105/10)*10 = 110, which is > max and
    // must be clamped back down instead of being sent as-is.
    const result = computeInitialBudget(101, 109, 10);
    expect(result).toBeLessThanOrEqual(109);
    expect(result).toBe(109);
  });

  it('returns the shared value when min === max, ignoring step', () => {
    expect(computeInitialBudget(500, 500, 100)).toBe(500);
  });

  it('stays within [min, max] for a normal range', () => {
    const result = computeInitialBudget(0, 200_000, 1_000);
    expect(result).toBeGreaterThanOrEqual(0);
    expect(result).toBeLessThanOrEqual(200_000);
  });
});
