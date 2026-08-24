import type { Filters, HealthInfo, IndexedDocument, SseEvent } from "./types";

const API_KEY_STORAGE = "sein-rag-api-key";

export function getApiKey(): string {
  return localStorage.getItem(API_KEY_STORAGE) ?? "";
}

export function setApiKey(key: string): void {
  if (key) localStorage.setItem(API_KEY_STORAGE, key);
  else localStorage.removeItem(API_KEY_STORAGE);
}

function headers(json = true): HeadersInit {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  const key = getApiKey();
  if (key) h["X-API-Key"] = key;
  return h;
}

export async function fetchHealth(): Promise<HealthInfo> {
  const res = await fetch("/api/health");
  if (!res.ok) throw new Error(`health ${res.status}`);
  return res.json();
}

export async function fetchDocuments(): Promise<IndexedDocument[]> {
  const res = await fetch("/api/documents", { headers: headers(false) });
  if (!res.ok) throw new Error(`documents ${res.status}`);
  const data = await res.json();
  return data.documents;
}

/**
 * POST /api/chat como stream SSE. EventSource solo soporta GET, así que
 * se parsea el stream de fetch a mano (frames "data: {...}\n\n").
 */
export async function streamChat(
  question: string,
  history: { role: string; content: string }[],
  filters: Filters,
  onEvent: (ev: SseEvent) => void,
  signal: AbortSignal,
  onQuota?: (remaining: number) => void,
): Promise<void> {
  const cleanFilters: Record<string, string> = {};
  if (filters.tipo) cleanFilters.tipo = filters.tipo;
  if (filters.ruc_usuario_libre) cleanFilters.ruc_usuario_libre = filters.ruc_usuario_libre;
  if (filters.usuario_libre) cleanFilters.usuario_libre = filters.usuario_libre;

  const res = await fetch("/api/chat", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      question,
      history,
      filters: Object.keys(cleanFilters).length ? cleanFilters : null,
    }),
    signal,
  });
  if (res.status === 401) throw new Error("API key inválida. Configúrala en el panel superior.");
  if (res.status === 429) {
    // El backend explica el límite y cuándo se renueva; ese texto es el error.
    const detail = await res
      .json()
      .then((d) => (typeof d?.detail === "string" ? d.detail : ""))
      .catch(() => "");
    onQuota?.(0);
    throw new Error(detail || "Alcanzaste el límite diario de preguntas. Vuelve mañana.");
  }
  if (!res.ok || !res.body) throw new Error(`Error del servidor (${res.status})`);

  const quotaHeader = res.headers.get("x-quota-remaining");
  if (quotaHeader !== null) onQuota?.(Number(quotaHeader));

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(6)) as SseEvent);
      } catch {
        // frame corrupto: se ignora, el evento "end" trae la respuesta completa
      }
    }
  }
}
