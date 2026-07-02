import { useCallback, useEffect, useRef, useState } from "react";
import { fetchHealth, getApiKey, setApiKey, streamChat } from "./api";
import { Message } from "./components/Message";
import { SourcesPanel } from "./components/SourcesPanel";
import type { ChatMessage, Filters, HealthInfo, SourceRef, SseEvent } from "./types";

const SUGGESTIONS = [
  "¿Cuál es la potencia contratada en el contrato más reciente?",
  "¿Qué cláusulas de resolución anticipada existen?",
  "Resume las condiciones de precio del contrato de un usuario libre",
];

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [filters, setFilters] = useState<Filters>({});
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [panelSources, setPanelSources] = useState<SourceRef[]>([]);
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [apiKeyInput, setApiKeyInput] = useState(getApiKey());
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const onCite = useCallback((s: SourceRef) => {
    setHighlighted(s.n);
    document.getElementById(`source-${s.n}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => setHighlighted(null), 2000);
  }, []);

  const send = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q || busy) return;
      setInput("");
      setBusy(true);
      setPanelSources([]);

      const history = messages
        .filter((m) => !m.error)
        .map((m) => ({ role: m.role, content: m.content }));

      setMessages((prev) => [
        ...prev,
        { role: "user", content: q },
        { role: "assistant", content: "", streaming: true, status: "Conectando" },
      ]);

      const updateLast = (patch: Partial<ChatMessage>) =>
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = { ...next[next.length - 1], ...patch };
          return next;
        });

      const appendToken = (text: string) =>
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, content: last.content + text };
          return next;
        });

      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await streamChat(q, history, filters, (ev: SseEvent) => {
          switch (ev.type) {
            case "status":
              updateLast({ status: ev.data.detail });
              break;
            case "sources":
              updateLast({ sources: ev.data.sources });
              setPanelSources(ev.data.sources);
              break;
            case "token":
              appendToken(ev.data.text);
              break;
            case "end":
              updateLast({
                content: ev.data.answer,
                sources: ev.data.sources,
                grounded: ev.data.grounded,
                noContext: ev.data.no_context,
                streaming: false,
                status: undefined,
              });
              setPanelSources(ev.data.sources);
              break;
            case "error":
              updateLast({ streaming: false, status: undefined, error: ev.data.message });
              break;
          }
        }, controller.signal);
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          updateLast({ streaming: false, status: undefined, error: (e as Error).message });
        } else {
          updateLast({ streaming: false, status: undefined });
        }
      } finally {
        setBusy(false);
        abortRef.current = null;
      }
    },
    [busy, filters, messages],
  );

  const stop = () => abortRef.current?.abort();

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1>Contratos SEIN</h1>
          <span className="subtitle">RAG local · usuarios libres · Osinergmin</span>
        </div>
        <div className="topbar-right">
          {health && (
            <span
              className={`health ${health.status === "ok" ? "health-ok" : "health-warn"}`}
              title={`LLM: ${health.llm} · Embeddings: ${health.embeddings} · Chunks: ${health.indexed_chunks ?? "?"}`}
            >
              ● {health.status === "ok" ? "operativo" : "degradado"}
              {health.indexed_chunks != null && ` · ${health.indexed_chunks} chunks`}
            </span>
          )}
          <button className="icon-btn" onClick={() => setShowSettings((v) => !v)} aria-label="Ajustes">
            ⚙
          </button>
        </div>
      </header>

      {showSettings && (
        <div className="settings">
          <label>
            API key (si el servidor la exige):{" "}
            <input
              type="password"
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              placeholder="X-API-Key"
            />
          </label>
          <button
            onClick={() => {
              setApiKey(apiKeyInput);
              setShowSettings(false);
            }}
          >
            Guardar
          </button>
        </div>
      )}

      <div className="filters-bar">
        <select
          value={filters.tipo ?? ""}
          onChange={(e) => setFilters((f) => ({ ...f, tipo: e.target.value || undefined }))}
        >
          <option value="">Tipo: todos</option>
          <option value="contrato">Solo contratos</option>
          <option value="adenda">Solo adendas</option>
        </select>
        <input
          className="ruc-input"
          placeholder="Filtrar por RUC (11 dígitos)"
          value={filters.ruc_usuario_libre ?? ""}
          maxLength={11}
          onChange={(e) => {
            const v = e.target.value.replace(/\D/g, "");
            setFilters((f) => ({ ...f, ruc_usuario_libre: v || undefined }));
          }}
        />
      </div>

      <main className="layout">
        <section className="chat">
          <div className="messages" ref={scrollRef}>
            {messages.length === 0 && (
              <div className="empty-state">
                <p>
                  Pregunta sobre los contratos de suministro eléctrico indexados. Cada respuesta
                  cita sus fuentes.
                </p>
                <div className="suggestions">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} onClick={() => send(s)} disabled={busy}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m, i) => (
              <Message key={i} message={m} onCite={onCite} />
            ))}
          </div>
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
          >
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input);
                }
              }}
              placeholder="Escribe tu pregunta… (Enter para enviar)"
              rows={2}
              disabled={busy}
            />
            {busy ? (
              <button type="button" className="btn-stop" onClick={stop}>
                Detener
              </button>
            ) : (
              <button type="submit" disabled={!input.trim()}>
                Enviar
              </button>
            )}
          </form>
        </section>
        <SourcesPanel
          sources={panelSources}
          highlighted={highlighted}
          onClose={() => setPanelSources([])}
        />
      </main>
    </div>
  );
}
