import { describe, expect, it } from 'vitest';
import {
  buildBehaviorMetricPayload,
  finalizeActiveHovers,
  initBehaviorTracking,
  readAndIncrementReturnCount,
  summarizeClicks,
  summarizeHovers,
  trackClick,
  trackHoverStart,
} from './behaviorMetrics';

function createMemoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => store.clear(),
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    get length() {
      return store.size;
    },
  } satisfies Storage;
}

describe('readAndIncrementReturnCount', () => {
  it('starts at 1 for a fresh storage', () => {
    expect(readAndIncrementReturnCount(createMemoryStorage())).toBe(1);
  });

  it('increments on each subsequent call against the same storage', () => {
    const storage = createMemoryStorage();
    readAndIncrementReturnCount(storage);
    readAndIncrementReturnCount(storage);
    expect(readAndIncrementReturnCount(storage)).toBe(3);
  });

  it('recovers to 1 if the stored value is corrupted/non-numeric', () => {
    const storage = createMemoryStorage();
    storage.setItem('aurel_return_count', 'not-a-number');
    expect(readAndIncrementReturnCount(storage)).toBe(1);
  });
});

describe('summarizeClicks', () => {
  it('converts a counts map into an array of {button, count}', () => {
    expect(summarizeClicks({ submit_application: 2, hero_cta: 1 })).toEqual([
      { button: 'submit_application', count: 2 },
      { button: 'hero_cta', count: 1 },
    ]);
  });

  it('returns an empty array for no clicks', () => {
    expect(summarizeClicks({})).toEqual([]);
  });
});

describe('summarizeHovers', () => {
  it('reports aggregated hover count and duration per section', () => {
    const state = new Map([
      ['hero', { enteredAt: null, totalMs: 1234.6, count: 2 }],
      ['services', { enteredAt: null, totalMs: 0, count: 0 }],
    ]);
    expect(summarizeHovers(state)).toEqual({
      hero: { hovers: 2, ms: 1235 },
      services: { hovers: 0, ms: 0 },
    });
  });
});

describe('finalizeActiveHovers', () => {
  it('folds an active hover interval into totalMs and closes it', () => {
    const state = new Map([['services', { enteredAt: 1000, totalMs: 500, count: 1 }]]);
    finalizeActiveHovers(state, 1300);
    expect(state.get('services')).toEqual({ enteredAt: null, totalMs: 800, count: 1 });
  });

  it('is idempotent: a second call does not add more time to an already-closed entry', () => {
    const state = new Map([['services', { enteredAt: 1000, totalMs: 500, count: 1 }]]);
    finalizeActiveHovers(state, 1300);
    finalizeActiveHovers(state, 1900);
    expect(state.get('services')?.totalMs).toBe(800);
    expect(state.get('services')?.enteredAt).toBeNull();
  });

  it('leaves already-closed entries untouched', () => {
    const state = new Map([['hero', { enteredAt: null, totalMs: 250, count: 1 }]]);
    finalizeActiveHovers(state, 5000);
    expect(state.get('hero')).toEqual({ enteredAt: null, totalMs: 250, count: 1 });
  });
});

describe('initBehaviorTracking (session reset)', () => {
  it('clears click counts and hover state left over from a previous visit', () => {
    initBehaviorTracking(createMemoryStorage());
    trackClick('hero_cta');
    trackClick('hero_cta');
    trackHoverStart('services');

    const beforeReset = buildBehaviorMetricPayload(1, 'test-capability');
    expect(beforeReset.clicked_buttons).toEqual([{ button: 'hero_cta', count: 2 }]);

    // Simulates SPA navigation home -> admin -> home: renderHome() calls
    // initBehaviorTracking() again on every mount.
    initBehaviorTracking(createMemoryStorage());

    const afterReset = buildBehaviorMetricPayload(2, 'test-capability');
    expect(afterReset.clicked_buttons).toEqual([]);
    expect(afterReset.cursor_hover_data).toEqual({});
  });

  it('does not reset return_count — it is a separate long-lived localStorage metric', () => {
    const storage = createMemoryStorage();
    initBehaviorTracking(storage);
    const first = buildBehaviorMetricPayload(1, 'test-capability').return_count;

    initBehaviorTracking(storage);
    const second = buildBehaviorMetricPayload(2, 'test-capability').return_count;

    expect(second).toBe((first ?? 0) + 1);
  });
});

describe('buildBehaviorMetricPayload + capability', () => {
  it('includes the given application_id and capability verbatim', () => {
    initBehaviorTracking(createMemoryStorage());
    const payload = buildBehaviorMetricPayload(7, 'one-time-token-abc');
    expect(payload.application_id).toBe(7);
    expect(payload.capability).toBe('one-time-token-abc');
  });
});

describe('buildBehaviorMetricPayload + active hover', () => {
  it('includes a still-active hover (no matching trackHoverEnd yet) in the payload', () => {
    initBehaviorTracking(createMemoryStorage());
    trackHoverStart('application_form');

    const payload = buildBehaviorMetricPayload(42, 'test-capability');
    const hoverData = payload.cursor_hover_data as Record<string, { hovers: number; ms: number }>;

    expect(hoverData.application_form).toBeDefined();
    expect(hoverData.application_form.hovers).toBe(1);
    expect(hoverData.application_form.ms).toBeGreaterThanOrEqual(0);
  });

  it('does not double count the same active hover across two payload builds', () => {
    initBehaviorTracking(createMemoryStorage());
    trackHoverStart('services');

    const first = buildBehaviorMetricPayload(1, 'test-capability').cursor_hover_data as Record<
      string,
      { hovers: number; ms: number }
    >;
    const second = buildBehaviorMetricPayload(2, 'test-capability').cursor_hover_data as Record<
      string,
      { hovers: number; ms: number }
    >;

    expect(second.services.ms).toBe(first.services.ms);
    expect(second.services.hovers).toBe(1);
  });
});
