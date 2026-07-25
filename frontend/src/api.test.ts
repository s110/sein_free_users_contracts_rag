import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchDocuments, fetchHealth, getApiKey, setApiKey, streamChat } from "./api";
import type { SseEvent } from "./types";

const enc = new TextEncoder();

/** Construye una Response cuyo body emite exactamente los `chunks` dados. */
function streamResponse(chunks: string[], status = 200): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
  return new Response(body, { status });
}

function mockFetch(impl: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const spy = vi.fn(impl);
  vi.stubGlobal("fetch", spy as unknown as typeof fetch);
  return spy;
}

async function collect(chunks: string[]): Promise<SseEvent[]> {
  mockFetch(() => streamResponse(chunks));
  const events: SseEvent[] = [];
  await streamChat("q", [], {}, (e) => events.push(e), new AbortController().signal);
  return events;
}

afterEach(() => vi.unstubAllGlobals());

describe("api key storage", () => {
  it("devuelve cadena vacía cuando no hay key guardada", () => {
    expect(getApiKey()).toBe("");
  });

  it("guarda y recupera la key", () => {
    setApiKey("secreto");
    expect(getApiKey()).toBe("secreto");
  });

  it("borra la entrada al guardar una key vacía", () => {
    setApiKey("secreto");
    setApiKey("");
    expect(getApiKey()).toBe("");
    expect(localStorage.getItem("sein-rag-api-key")).toBeNull();
  });
});

describe("fetchHealth / fetchDocuments", () => {
  it("parsea la respuesta de health", async () => {
    mockFetch(() => new Response(JSON.stringify({ status: "ok", version: "1.0.0" })));
    await expect(fetchHealth()).resolves.toMatchObject({ status: "ok" });
  });

  it("lanza error cuando health responde !ok", async () => {
    mockFetch(() => new Response("boom", { status: 503 }));
    await expect(fetchHealth()).rejects.toThrow(/503/);
  });

  it("no manda Content-Type en GET de documentos pero sí la API key", async () => {
    setApiKey("k1");
    const spy = mockFetch(() => new Response(JSON.stringify({ count: 0, documents: [] })));
    await fetchDocuments();
    const headers = spy.mock.calls[0][1]?.headers as Record<string, string>;
    expect(headers["X-API-Key"]).toBe("k1");
    expect(headers["Content-Type"]).toBeUndefined();
  });

  it("lanza error cuando documentos responde !ok", async () => {
    mockFetch(() => new Response("nope", { status: 401 }));
    await expect(fetchDocuments()).rejects.toThrow(/401/);
  });
});

describe("streamChat: parseo SSE", () => {
  it("emite los eventos de un stream bien formado", async () => {
    const events = await collect([
      'data: {"type":"status","data":{"step":"analyze","detail":"Analizando"}}\n\n',
      'data: {"type":"token","data":{"text":"Hola"}}\n\n',
      'data: {"type":"end","data":{"answer":"Hola","grounded":true,"no_context":false,"rewrites":0,"sources":[]}}\n\n',
    ]);
    expect(events.map((e) => e.type)).toEqual(["status", "token", "end"]);
  });

  it("reensambla frames partidos entre chunks de red", async () => {
    const events = await collect([
      'data: {"type":"tok',
      'en","data":{"text":"par',
      'tido"}}\n\n',
    ]);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: "token", data: { text: "partido" } });
  });

  it("entrega varios frames que llegan en un solo chunk", async () => {
    const events = await collect([
      'data: {"type":"token","data":{"text":"a"}}\n\ndata: {"type":"token","data":{"text":"b"}}\n\n',
    ]);
    expect(events).toHaveLength(2);
  });

  it("ignora frames corruptos sin abortar el stream", async () => {
    const events = await collect([
      "data: {no-es-json}\n\n",
      'data: {"type":"token","data":{"text":"ok"}}\n\n',
    ]);
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("token");
  });

  it("ignora comentarios y frames sin línea data:", async () => {
    const events = await collect([
      ": keep-alive\n\n",
      'event: ping\ndata: {"type":"token","data":{"text":"x"}}\n\n',
    ]);
    expect(events).toHaveLength(1);
  });

  it("descarta un frame final sin terminador \\n\\n", async () => {
    const events = await collect([
      'data: {"type":"token","data":{"text":"completo"}}\n\ndata: {"type":"token","data":{"text":"trunc',
    ]);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ data: { text: "completo" } });
  });

  it("decodifica UTF-8 multibyte partido entre chunks", async () => {
    const full = enc.encode('data: {"type":"token","data":{"text":"electrificación"}}\n\n');
    const cut = 45; // cae dentro de la secuencia multibyte de "ó"
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(full.slice(0, cut));
        c.enqueue(full.slice(cut));
        c.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(() => new Response(body)) as unknown as typeof fetch);
    const events: SseEvent[] = [];
    await streamChat("q", [], {}, (e) => events.push(e), new AbortController().signal);
    expect(events[0]).toMatchObject({ data: { text: "electrificación" } });
  });
});

describe("streamChat: request", () => {
  it("omite filtros vacíos enviando filters=null", async () => {
    const spy = mockFetch(() => streamResponse([]));
    await streamChat("pregunta", [], {}, () => {}, new AbortController().signal);
    const body = JSON.parse(spy.mock.calls[0][1]?.body as string);
    expect(body.filters).toBeNull();
    expect(body.question).toBe("pregunta");
  });

  it("envía sólo los filtros con valor", async () => {
    const spy = mockFetch(() => streamResponse([]));
    await streamChat(
      "q",
      [],
      { tipo: "contrato", ruc_usuario_libre: "" },
      () => {},
      new AbortController().signal,
    );
    const body = JSON.parse(spy.mock.calls[0][1]?.body as string);
    expect(body.filters).toEqual({ tipo: "contrato" });
  });

  it("da un mensaje accionable en 401", async () => {
    mockFetch(() => new Response("", { status: 401 }));
    await expect(
      streamChat("q", [], {}, () => {}, new AbortController().signal),
    ).rejects.toThrow(/API key/);
  });

  it("propaga otros errores del servidor con su status", async () => {
    mockFetch(() => new Response("", { status: 500 }));
    await expect(
      streamChat("q", [], {}, () => {}, new AbortController().signal),
    ).rejects.toThrow(/500/);
  });
});
