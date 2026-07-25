import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
});

// jsdom no implementa scrollTo/scrollIntoView; App los usa al llegar mensajes.
Element.prototype.scrollTo = Element.prototype.scrollTo ?? (() => {});
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});
