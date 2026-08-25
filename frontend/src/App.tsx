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
  // Cerrar el panel no descarta las fuentes: se puede reabrir desde la
  // barra superior (antes, cerrarlo era irreversible hasta la siguiente
  // pregunta).
  const [panelOpen, setPanelOpen] = useState(true);
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [apiKeyInput, setApiKeyInput] = useState(getApiKey());
  // null = sin cuota conocida (modo con API key o backend sin modo público).
  const [quotaRemaining, setQuotaRemaining] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Link mágico de acceso ilimitado: contracts.../#acceso=CLAVE guarda la
    // clave una sola vez y limpia la URL. Va en el fragmento (#) a propósito:
    // los fragmentos no llegan al servidor ni a los logs de nginx/Cloudflare.
    const m = window.location.hash.match(/^#acceso=(.+)$/);
    if (m) {
      const clave = decodeURIComponent(m[1]);
      setApiKey(clave);
      setApiKeyInput(clave);
      history.replaceState(null, "", window.location.pathname + window.location.search);
    }
    fetchHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: reduced ? "auto" : "smooth",
    });
  }, [messages]);

  const onCite = useCallback((s: SourceRef) => {
    setPanelOpen(true);
    setHighlighted(s.n);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document
      .getElementById(`source-${s.n}`)
      ?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
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
      // Si el stream se corta sin evento `end` (backend OOM-killed, timeout de
      // nginx, frame corrupto), el mensaje se quedaba con streaming:true para
      // siempre: cursor parpadeando, sin error y sin pie, como si la respuesta
      // siguiera llegando.
      let finished = false;
      try {
        await streamChat(q, history, filters, (ev: SseEvent) => {
          switch (ev.type) {
            case "status":
              updateLast({ status: ev.data.detail });
              break;
            case "sources":
              updateLast({ sources: ev.data.sources });
              setPanelSources(ev.data.sources);
              setPanelOpen(true);
              break;
            case "token":
              appendToken(ev.data.text);
              break;
            case "end":
              finished = true;
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
              finished = true;
              updateLast({ streaming: false, status: undefined, error: ev.data.message });
              break;
          }
        }, controller.signal, setQuotaRemaining);
        if (!finished) {
          updateLast({
            streaming: false,
            status: undefined,
            error: "La conexión se cortó antes de terminar la respuesta. Vuelve a intentarlo.",
          });
        }
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
          {panelSources.length > 0 && !panelOpen && (
            <button className="btn-fuentes" onClick={() => setPanelOpen(true)}>
              Fuentes ({panelSources.length})
            </button>
          )}
          <button className="icon-btn" onClick={() => setShowSettings((v) => !v)} aria-label="Ajustes">
            ⚙
          </button>
        </div>
      </header>

      {showSettings && (
        <div className="settings">
          <label>
            Acceso sin límite diario (clave privada del administrador):{" "}
            <input
              type="password"
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              placeholder="X-API-Key"
              autoComplete="off"
              spellCheck={false}
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
          aria-label="Filtrar por tipo de documento"
          onChange={(e) => setFilters((f) => ({ ...f, tipo: e.target.value || undefined }))}
        >
          <option value="">Tipo: todos</option>
          <option value="contrato">Solo contratos</option>
          <option value="adenda">Solo adendas</option>
        </select>
        <input
          className="razon-input"
          placeholder="Filtrar por razón social, ej. lavandería landeo"
          aria-label="Filtrar por razón social del usuario libre"
          value={filters.usuario_libre ?? ""}
          onChange={(e) =>
            setFilters((f) => ({ ...f, usuario_libre: e.target.value || undefined }))
          }
        />
        <input
          className="ruc-input"
          placeholder="Filtrar por RUC, ej. 20100017491"
          aria-label="Filtrar por RUC del usuario libre (11 dígitos)"
          inputMode="numeric"
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
              aria-label="Tu pregunta sobre los contratos"
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
          {quotaRemaining !== null && (
            <p className="quota-note" aria-live="polite">
              {quotaRemaining > 0
                ? `Te quedan ${quotaRemaining} pregunta${quotaRemaining === 1 ? "" : "s"} hoy.`
                : "Agotaste tus preguntas de hoy. La cuota se renueva a las 00:00 UTC."}
            </p>
          )}
        </section>
        {panelOpen && (
          <SourcesPanel
            sources={panelSources}
            highlighted={highlighted}
            onClose={() => setPanelOpen(false)}
          />
        )}
      </main>
    </div>
  );
}
