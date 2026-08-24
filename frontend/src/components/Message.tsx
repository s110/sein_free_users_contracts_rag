import type { ChatMessage, SourceRef } from "../types";

/** Renderiza el texto de la respuesta convirtiendo marcadores [n] en chips de cita. */
function renderWithCitations(
  text: string,
  sources: SourceRef[] | undefined,
  onCite: (s: SourceRef) => void,
) {
  const parts = text.split(/(\[\d+\])/g);
  return parts.map((part, i) => {
    const m = part.match(/^\[(\d+)\]$/);
    if (!m) return <span key={i}>{part}</span>;
    const n = parseInt(m[1], 10);
    const source = sources?.find((s) => s.n === n);
    if (!source) return <span key={i}>{part}</span>;
    return (
      <button
        key={i}
        className="citation-chip"
        title={`${source.source_file}${source.page_start ? ` — pág. ${source.page_start}` : ""}`}
        aria-label={`Ver fuente ${n}: ${source.source_file}`}
        onClick={() => onCite(source)}
      >
        {n}
      </button>
    );
  });
}

interface Props {
  message: ChatMessage;
  onCite: (s: SourceRef) => void;
}

export function Message({ message, onCite }: Props) {
  if (message.role === "user") {
    return <div className="msg msg-user">{message.content}</div>;
  }
  return (
    <div className="msg msg-assistant">
      {message.status && message.streaming && !message.content && (
        <div className="agent-status" aria-live="polite">
          <span className="spinner" aria-hidden="true" /> {message.status}…
        </div>
      )}
      <div className="msg-body">
        {renderWithCitations(message.content, message.sources, onCite)}
        {message.streaming && message.content && <span className="cursor">▌</span>}
      </div>
      {message.error && (
        <div className="msg-error" role="alert">
          ⚠ {message.error}
        </div>
      )}
      {!message.streaming && !message.error && message.content && (
        <div className="msg-footer">
          {message.grounded === true && (
            <span className="badge badge-ok" title="El verificador confirmó que la respuesta está sustentada en las fuentes">
              ✓ Verificado contra fuentes
            </span>
          )}
          {message.grounded === false && (
            <span className="badge badge-warn" title="El verificador no pudo confirmar todas las afirmaciones — revisa las citas">
              ⚠ Verificación no concluyente
            </span>
          )}
          {message.noContext && <span className="badge badge-muted">Sin contexto en el índice</span>}
        </div>
      )}
    </div>
  );
}
