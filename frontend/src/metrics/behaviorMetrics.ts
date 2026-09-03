/**
 * Lightweight, first-party behavior metrics — no third-party analytics,
 * nothing leaves the browser except the single POST to /api/behavior-metrics
 * after a successful Application is created.
 *
 * Deliberately NOT collected: form field contents, precise mouse
 * coordinates, device/browser fingerprinting. Only coarse aggregate
 * counters (clicks per named button, hover count/duration per named
 * section, a localStorage-backed return-visit counter, and elapsed time).
 */

import type { BehaviorMetricCreatePayload } from '../api/types';

interface HoverEntry {
  enteredAt: number | null;
  totalMs: number;
  count: number;
}

const RETURN_COUNT_KEY = 'aurel_return_count';

const clickCounts: Record<string, number> = {};
const hoverState = new Map<string, HoverEntry>();

let pageLoadedAt = Date.now();
let returnCountForThisVisit = 0;

/**
 * Call once when the home page mounts. Resets every session-scoped counter
 * (clicks, hover counts/durations, any in-progress hover, elapsed time) so
 * SPA navigation like home -> admin -> home never carries a previous
 * visit's metrics into a new Application. return_count is the one
 * exception: it's a long-lived localStorage counter, not session state, so
 * it's read/incremented here but never zeroed.
 */
export function initBehaviorTracking(storage: Storage = window.localStorage): void {
  pageLoadedAt = Date.now();
  for (const key of Object.keys(clickCounts)) {
    delete clickCounts[key];
  }
  hoverState.clear();
  returnCountForThisVisit = readAndIncrementReturnCount(storage);
}

export function trackClick(button: string): void {
  clickCounts[button] = (clickCounts[button] ?? 0) + 1;
}

export function trackHoverStart(section: string): void {
  const entry = hoverState.get(section) ?? { enteredAt: null, totalMs: 0, count: 0 };
  entry.enteredAt = Date.now();
  entry.count += 1;
  hoverState.set(section, entry);
}

export function trackHoverEnd(section: string): void {
  const entry = hoverState.get(section);
  if (!entry || entry.enteredAt === null) return;
  entry.totalMs += Date.now() - entry.enteredAt;
  entry.enteredAt = null;
}

/** Pure — unit tested in metrics/behaviorMetrics.test.ts. */
export function readAndIncrementReturnCount(storage: Storage): number {
  const raw = storage.getItem(RETURN_COUNT_KEY);
  const previous = raw ? Number.parseInt(raw, 10) : 0;
  const next = Number.isFinite(previous) && previous > 0 ? previous + 1 : 1;
  storage.setItem(RETURN_COUNT_KEY, String(next));
  return next;
}

/** Pure — unit tested in metrics/behaviorMetrics.test.ts. */
export function summarizeClicks(
  counts: Record<string, number>,
): Array<{ button: string; count: number }> {
  return Object.entries(counts).map(([button, count]) => ({ button, count }));
}

/** Pure — unit tested in metrics/behaviorMetrics.test.ts. */
export function summarizeHovers(
  state: Map<string, HoverEntry>,
): Record<string, { hovers: number; ms: number }> {
  const summary: Record<string, { hovers: number; ms: number }> = {};
  for (const [section, entry] of state.entries()) {
    summary[section] = { hovers: entry.count, ms: Math.round(entry.totalMs) };
  }
  return summary;
}

/**
 * Folds any still-active hover (mouse currently resting on a tracked
 * section when the payload is built) into totalMs and closes it out.
 * Pure with respect to `now` — unit tested in metrics/behaviorMetrics.test.ts.
 * Idempotent: closed entries (enteredAt === null) are left untouched, so
 * calling this twice never double-counts the same interval.
 */
export function finalizeActiveHovers(state: Map<string, HoverEntry>, now: number = Date.now()): void {
  for (const entry of state.values()) {
    if (entry.enteredAt !== null) {
      entry.totalMs += now - entry.enteredAt;
      entry.enteredAt = null;
    }
  }
}

export function buildBehaviorMetricPayload(
  applicationId: number,
  capability: string,
): BehaviorMetricCreatePayload {
  finalizeActiveHovers(hoverState);
  const timeOnPageSeconds = Math.max(0, Math.round((Date.now() - pageLoadedAt) / 1000));
  return {
    application_id: applicationId,
    capability,
    time_on_page: timeOnPageSeconds,
    clicked_buttons: summarizeClicks(clickCounts),
    cursor_hover_data: summarizeHovers(hoverState),
    return_count: returnCountForThisVisit,
  };
}
