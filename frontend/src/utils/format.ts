/** Pure formatting helpers — unit tested in utils/format.test.ts. */

const currencyFormatter = new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 0,
});

/** Formats a whole-ruble amount for display, e.g. 150000 -> "150 000 ₽". */
export function formatBudget(amount: number): string {
  return `${currencyFormatter.format(Math.round(amount))} ₽`;
}

/**
 * Picks a "nice" slider step for a given budget range so the handle moves
 * in round increments instead of by 1 ruble at a time.
 */
export function pickBudgetStep(min: number, max: number): number {
  const span = Math.max(max - min, 0);
  if (span <= 0) return 1;
  if (span <= 1_000) return 10;
  if (span <= 10_000) return 100;
  if (span <= 100_000) return 500;
  return 1_000;
}

/** Clamps a value into [min, max]. Falls back to min if min > max (should
 * never happen — the backend enforces budget_min <= budget_max). */
export function clampToRange(value: number, min: number, max: number): number {
  if (min > max) return min;
  return Math.min(Math.max(value, min), max);
}

/**
 * Picks a sensible starting budget for the slider: the range midpoint
 * rounded to the nearest step, clamped back into [min, max].
 *
 * Rounding a midpoint to a step can overshoot the range — e.g. min=101,
 * max=109, step=10: midpoint 105 rounds to 110, which is outside the
 * range — so the clamp is not optional.
 */
export function computeInitialBudget(min: number, max: number, step: number): number {
  if (min === max) return min;
  const midpoint = (min + max) / 2;
  const rounded = Math.round(midpoint / step) * step;
  return clampToRange(rounded, min, max);
}
