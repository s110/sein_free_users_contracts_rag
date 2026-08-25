import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { SourceRef } from "../types";

/**
 * Los marcadores [n] se convierten en enlaces internos #cita-n ANTES de
 * parsear el Markdown: así el chip de cita funciona también dentro de una
 * celda de tabla o de un tramo en negrita, donde un split por regex sobre
 * el texto plano jamás llegaba. El lookahead (?!\() evita comerse un enlace
 * Markdown legítimo `[1](https://...)`.
 */
function citasComoEnlaces(text: string): string {
  return text.replace(/\[(\d{1,2})\](?!\()/g, "[$1](#cita-$1)");
}

/** Solo http(s): un source_url manipulado no debe volverse javascript: */
function safeHref(url: string | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url, window.location.origin);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

interface Props {
  text: string;
  sources?: SourceRef[];
  onCite?: (s: SourceRef) => void;
}

/** Markdown GFM (tablas, negritas, listas) con chips de cita [n] clicables. */
export function Markdown({ text, sources, onCite }: Props) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ href, children }) => {
          const m = href?.match(/^#cita-(\d+)$/);
          if (m) {
            const n = parseInt(m[1], 10);
            const source = sources?.find((s) => s.n === n);
            if (!source || !onCite) return <span>[{n}]</span>;
            return (
              <button
                type="button"
                className="citation-chip"
                title={`${source.source_file}${source.page_start ? ` — pág. ${source.page_start}` : ""}`}
                aria-label={`Ver fuente ${n}: ${source.source_file}`}
                onClick={() => onCite(source)}
              >
                {n}
              </button>
            );
          }
          const safe = safeHref(href);
          if (!safe) return <span>{children}</span>;
          return (
            <a href={safe} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          );
        },
        // Una tabla ancha desborda el globo del mensaje: scroll propio.
        table: ({ children }) => (
          <div className="table-wrap">
            <table>{children}</table>
          </div>
        ),
      }}
    >
      {citasComoEnlaces(text)}
    </ReactMarkdown>
  );
}
