import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const enc = new TextEncoder();

function sse(chunks: string[], status = 200): Response {
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const s of chunks) c.enqueue(enc.encode(s));
      c.close();
    },
  });
  return new Response(body, { status });
}

const HEALTH = { status: "ok", version: "1.0.0", llm: "qwen3:4b", embeddings: "bge-m3" };

function installFetch(chatResponse: () => Response, health: unknown = HEALTH) {
  const spy = vi.fn((url: string, init?: RequestInit) => {
    void init;
    if (String(url).includes("/api/health")) return Promise.resolve(Response.json(health));
    return Promise.resolve(chatResponse());
  });
  vi.stubGlobal("fetch", spy as unknown as typeof fetch);
  return spy;
}

beforeEach(() => {
  vi.stubGlobal("scrollTo", () => {});
});
afterEach(() => vi.unstubAllGlobals());

async function ask(question = "¿Potencia contratada?") {
  const box = screen.getByPlaceholderText(/Escribe tu pregunta/);
  await userEvent.type(box, question);
  await userEvent.click(screen.getByRole("button", { name: "Enviar" }));
}

describe("App", () => {
  it("muestra el estado de salud del backend", async () => {
    installFetch(() => sse([]));
    render(<App />);
    expect(await screen.findByText(/operativo/)).toBeInTheDocument();
  });

  it("marca el backend como degradado", async () => {
    installFetch(() => sse([]), { ...HEALTH, status: "degraded" });
    render(<App />);
    expect(await screen.findByText(/degradado/)).toBeInTheDocument();
  });

  it("sobrevive a un /api/health caído", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("network"))) as unknown as typeof fetch,
    );
    render(<App />);
    expect(await screen.findByPlaceholderText(/Escribe tu pregunta/)).toBeInTheDocument();
  });

  it("mantiene Enviar deshabilitado mientras el input está vacío", async () => {
    installFetch(() => sse([]));
    render(<App />);
    expect(screen.getByRole("button", { name: "Enviar" })).toBeDisabled();
  });

  it("renderiza tokens en streaming y la respuesta final con su fuente", async () => {
    installFetch(() =>
      sse([
        'data: {"type":"status","data":{"step":"retrieve","detail":"Buscando"}}\n\n',
        'data: {"type":"token","data":{"text":"La potencia "}}\n\n',
        'data: {"type":"token","data":{"text":"es 10 MW [1]."}}\n\n',
        'data: {"type":"end","data":{"answer":"La potencia es 10 MW [1].","grounded":true,"no_context":false,"rewrites":0,"sources":[{"n":1,"source_file":"c1.md","doc_id":"d1","section":null,"page_start":2,"page_end":2,"usuario_libre":null,"suministrador":null,"fecha_suscripcion":null,"tipo":null,"source_url":null,"snippet":"s"}]}}\n\n',
      ]),
    );
    render(<App />);
    await ask();
    expect(await screen.findByText(/La potencia es 10 MW/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("c1.md")).toBeInTheDocument());
    expect(screen.getByText(/Verificado contra fuentes/)).toBeInTheDocument();
  });

  it("muestra el error del backend sin romper la conversación", async () => {
    installFetch(() =>
      sse(['data: {"type":"error","data":{"message":"Ollama no responde"}}\n\n']),
    );
    render(<App />);
    await ask();
    expect(await screen.findByText(/Ollama no responde/)).toBeInTheDocument();
  });

  it("reporta un 401 como problema de API key", async () => {
    installFetch(() => new Response("", { status: 401 }));
    render(<App />);
    await ask();
    expect(await screen.findByText(/API key/)).toBeInTheDocument();
  });

  it("envía el historial previo en la segunda pregunta", async () => {
    const spy = installFetch(() =>
      sse([
        'data: {"type":"end","data":{"answer":"ok","grounded":null,"no_context":false,"rewrites":0,"sources":[]}}\n\n',
      ]),
    );
    render(<App />);
    await ask("primera");
    await screen.findByText("ok");
    await ask("segunda");
    await waitFor(() => {
      const chatCalls = spy.mock.calls.filter((c) => String(c[0]).includes("/api/chat"));
      expect(chatCalls).toHaveLength(2);
      const body = JSON.parse(
        chatCalls[1][1]!.body as string,
      );
      expect(body.history).toEqual([
        { role: "user", content: "primera" },
        { role: "assistant", content: "ok" },
      ]);
    });
  });

  it("aplica los filtros de tipo y RUC en la petición", async () => {
    const spy = installFetch(() =>
      sse([
        'data: {"type":"end","data":{"answer":"ok","grounded":null,"no_context":false,"rewrites":0,"sources":[]}}\n\n',
      ]),
    );
    render(<App />);
    await userEvent.selectOptions(screen.getByRole("combobox"), "adenda");
    await userEvent.type(screen.getByPlaceholderText(/Filtrar por RUC/), "20AB123456789");
    await ask();
    await waitFor(() => {
      const call = spy.mock.calls.find((c) => String(c[0]).includes("/api/chat"))!;
      const body = JSON.parse(call[1]!.body as string);
      // el input descarta no-dígitos y recorta a 11
      expect(body.filters).toEqual({ tipo: "adenda", ruc_usuario_libre: "20123456789" });
    });
  });

  it("persiste la API key desde el panel de ajustes", async () => {
    installFetch(() => sse([]));
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Ajustes" }));
    await userEvent.type(screen.getByPlaceholderText("X-API-Key"), "clave-123");
    await userEvent.click(screen.getByRole("button", { name: "Guardar" }));
    expect(localStorage.getItem("sein-rag-api-key")).toBe("clave-123");
  });

  it("dispara una sugerencia con un solo click", async () => {
    const spy = installFetch(() =>
      sse([
        'data: {"type":"end","data":{"answer":"resp","grounded":null,"no_context":false,"rewrites":0,"sources":[]}}\n\n',
      ]),
    );
    render(<App />);
    const suggestion = screen.getByRole("button", { name: /potencia contratada/i });
    await userEvent.click(suggestion);
    await waitFor(() =>
      expect(spy.mock.calls.some((c) => String(c[0]).includes("/api/chat"))).toBe(true),
    );
  });
});

describe("App: stream cortado", () => {
  it("marca error cuando el stream termina sin evento end", async () => {
    // Backend OOM-killed a mitad: el mensaje se quedaba con el cursor
    // parpadeando para siempre, sin error y sin pie.
    installFetch(() => sse(['data: {"type":"token","data":{"text":"La potencia es"}}\n\n']));
    render(<App />);
    await ask();
    expect(await screen.findByText(/conexión se cortó/i)).toBeInTheDocument();
  });

  it("no marca error cuando sí llegó el end", async () => {
    installFetch(() =>
      sse([
        'data: {"type":"end","data":{"answer":"listo","grounded":null,"no_context":false,"rewrites":0,"sources":[]}}\n\n',
      ]),
    );
    render(<App />);
    await ask();
    await screen.findByText("listo");
    expect(screen.queryByText(/conexión se cortó/i)).not.toBeInTheDocument();
  });

  it("no marca error cuando el backend reportó su propio error", async () => {
    installFetch(() => sse(['data: {"type":"error","data":{"message":"Ollama caído"}}\n\n']));
    render(<App />);
    await ask();
    expect(await screen.findByText(/Ollama caído/)).toBeInTheDocument();
    expect(screen.queryByText(/conexión se cortó/i)).not.toBeInTheDocument();
  });
});

describe("App: link mágico de acceso", () => {
  it("guarda la clave del fragmento #acceso= y limpia la URL", async () => {
    window.location.hash = "#acceso=clave-magica-123";
    localStorage.clear();
    const { getApiKey } = await import("./api");
    render(<App />);
    await waitFor(() => expect(getApiKey()).toBe("clave-magica-123"));
    expect(window.location.hash).toBe("");
    window.location.hash = "";
  });
});
