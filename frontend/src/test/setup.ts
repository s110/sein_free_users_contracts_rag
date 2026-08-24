import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// El jsdom actual no expone localStorage (todos los tests morían en el
// afterEach). Stub mínimo con la misma semántica que usa la app.
if (typeof globalThis.localStorage === "undefined") {
  const store = new Map<string, string>();
  const stub = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", { value: stub, configurable: true });
  Object.defineProperty(globalThis.window, "localStorage", { value: stub, configurable: true });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
});

// jsdom no implementa scrollTo/scrollIntoView; App los usa al llegar mensajes.
Element.prototype.scrollTo = Element.prototype.scrollTo ?? (() => {});
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});

// jsdom tampoco implementa matchMedia; App lo consulta para
// prefers-reduced-motion. En tests se comporta como "sin preferencia".
if (typeof window.matchMedia === "undefined") {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      onchange: null,
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
