import type { ChatMessage, SourceRef } from "../types";
import { Markdown } from "./Markdown";

interface Props {
  message: ChatMessage;
  /** Recibe también las fuentes DEL MENSAJE: el panel muestra las de la
   *  respuesta citada, no las de la última pregunta. */
  onCite: (s: SourceRef, sources: SourceRef[]) => void;
  onShowSources: (sources: SourceRef[]) => void;
}

export function Message({ message, onCite, onShowSources }: Props) {
  if (message.role === "user") {
    return <div className="msg msg-user">{message.content}</div>;
  }
  const sources = message.sources ?? [];
  const issues = message.claimIssues ?? [];
  const total = message.claimsTotal ?? 0;
  const ok = message.claimsOk ?? 0;
  const refutadas = issues.filter((i) => i.estado === "refutada").length;
  return (
    <div className="msg msg-assistant">
      {message.status && message.streaming && !message.content && (
        <div className="agent-status" aria-live="polite">
          <span className="spinner" aria-hidden="true" /> {message.status}…
        </div>
      )}
      <div className="msg-body">
        <Markdown
          text={message.content}
          sources={sources}
          onCite={(s) => onCite(s, sources)}
        />
        {message.streaming && message.content && <span className="cursor">▌</span>}
      </div>
      {message.error && (
        <div className="msg-error" role="alert">
          ⚠ {message.error}
        </div>
      )}
      {!message.streaming && !message.error && message.content && (
        <div className="msg-footer">
          {message.grounded === true && total > 0 && (
            <span
              className="badge badge-ok"
              title="Cada afirmación se contrastó por separado contra el fragmento que cita"
            >
              ✓ {total} afirmacion{total === 1 ? "" : "es"} contrastada
              {total === 1 ? "" : "s"}
            </span>
          )}
          {issues.length > 0 && (
            <span
              className={`badge ${refutadas > 0 ? "badge-err" : "badge-warn"}`}
              title="Detalle debajo: qué afirmación y por qué no se pudo sustentar"
            >
              {refutadas > 0 ? "✗" : "⚠"} {issues.length} de {total} sin sustento
              {ok > 0 && ` · ${ok} verificada${ok === 1 ? "" : "s"}`}
            </span>
          )}
          {message.noContext && <span className="badge badge-muted">Sin contexto en el índice</span>}
          {sources.length > 0 && (
            <button
              type="button"
              className="badge badge-fuentes"
              onClick={() => onShowSources(sources)}
              title="Abrir en el panel las fuentes de esta respuesta"
            >
              Fuentes ({sources.length})
            </button>
          )}
        </div>
      )}
      {!message.streaming && issues.length > 0 && (
        // Decir cuál dato no se pudo sustentar vale más que una insignia verde
        // sobre una respuesta con una cifra mal atribuida.
        <details className="claim-issues">
          <summary>Qué no se pudo verificar</summary>
          <ul>
            {issues.map((it, i) => (
              <li key={i} className={`claim-${it.estado}`}>
                <span className="claim-estado">
                  {it.estado === "refutada"
                    ? "Contradice la fuente"
                    : it.estado === "sin_cita"
                      ? "Sin cita"
                      : "No está en la fuente citada"}
                </span>
                <span className="claim-texto">{it.texto}</span>
                {it.motivo && <span className="claim-motivo">{it.motivo}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
