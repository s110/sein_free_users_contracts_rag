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
    </div>
  );
}
