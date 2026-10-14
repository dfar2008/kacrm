import { readModePreference, resolveAppearance, subscribeSystemAppearance } from "./appearance";

const storage = (entries: Record<string, string>) => ({ getItem: (key: string) => entries[key] ?? null });

test("new users follow the system and legacy manual selections are retained", () => {
  expect(readModePreference(storage({}))).toBe("system");
  expect(readModePreference(storage({ "hermes.mode": "light" }))).toBe("light");
  expect(readModePreference(storage({ "hermes.mode": "dark" }))).toBe("dark");
});

test("KaCRM preference overrides the legacy key, including system mode", () => {
  expect(readModePreference(storage({ "kacrm.theme.mode": "system", "hermes.mode": "dark" }))).toBe("system");
});

test("blocked storage and invalid preferences safely follow the system", () => {
  expect(readModePreference({ getItem: () => { throw new Error("blocked"); } })).toBe("system");
  expect(readModePreference(storage({ "kacrm.theme.mode": "invalid" }))).toBe("system");
});

test("manual appearance overrides OS changes while system appearance follows them", () => {
  expect(resolveAppearance("system", true)).toBe("dark");
  expect(resolveAppearance("system", false)).toBe("light");
  expect(resolveAppearance("light", true)).toBe("light");
  expect(resolveAppearance("dark", false)).toBe("dark");
});

test("system subscription publishes changes and removes its listener on cleanup", () => {
  let listener: ((event: { matches: boolean }) => void) | undefined;
  let removed = false;
  const media = {
    matches: true,
    addEventListener: (_type: "change", callback: typeof listener) => { listener = callback; },
    removeEventListener: (_type: "change", callback: typeof listener) => {
      removed = callback === listener; listener = undefined;
    },
  };
  const changes: boolean[] = [];
  const cleanup = subscribeSystemAppearance(media, value => changes.push(value));
  listener?.({ matches: false });
  listener?.({ matches: true });
  expect(changes).toEqual([false, true]);
  cleanup();
  expect(removed).toBe(true);
  expect(listener).toBeUndefined();
});
