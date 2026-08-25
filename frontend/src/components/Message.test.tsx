import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ChatMessage, SourceRef } from "../types";
import { Message } from "./Message";

function source(n: number, extra: Partial<SourceRef> = {}): SourceRef {
  return {
    n,
    source_file: `contrato-${n}.md`,
    doc_id: `doc-${n}`,
    section: null,
    page_start: null,
    page_end: null,
    usuario_libre: null,
    suministrador: null,
    fecha_suscripcion: null,
    tipo: null,
    source_url: null,
    snippet: "",
    ...extra,
  };
}

function assistant(extra: Partial<ChatMessage> = {}): ChatMessage {
  return { role: "assistant", content: "", ...extra };
}

describe("Message", () => {
  it("renderiza el mensaje del usuario como texto plano", () => {
    render(
      <Message message={{ role: "user", content: "¿Cuál es la potencia?" }} onCite={() => {}} onShowSources={() => {}} />,
    );
    expect(screen.getByText("¿Cuál es la potencia?")).toBeInTheDocument();
  });

  it("no interpreta HTML del modelo (sin XSS)", () => {
    const { container } = render(
      <Message
        message={assistant({ content: "<img src=x onerror=alert(1)> hola" })}
        onCite={() => {}} onShowSources={() => {}}
      />,
    );
    // react-markdown sin rehype-raw descarta los nodos HTML: no hay <img>
    // ni ejecución; el texto legítimo alrededor sobrevive.
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("hola");
  });

  it("renderiza Markdown: negritas y tablas GFM", () => {
    const md = "La potencia es **1015 kW**.\n\n| Cliente | kW |\n|---|---|\n| ACME | 1000 |";
    const { container } = render(
      <Message message={assistant({ content: md })} onCite={() => {}} onShowSources={() => {}} />,
    );
    expect(container.querySelector("strong")?.textContent).toBe("1015 kW");
    expect(container.querySelector(".table-wrap table")).not.toBeNull();
    expect(screen.getByRole("cell", { name: "ACME" })).toBeInTheDocument();
    expect(container.textContent).not.toContain("**");
  });

  it("los chips de cita funcionan dentro de una celda de tabla", async () => {
    const onCite = vi.fn();
    const md = "| Cliente | kW |\n|---|---|\n| ACME [1] | 1000 |";
    render(
      <Message
        message={assistant({ content: md, sources: [source(1)] })}
        onCite={onCite}
        onShowSources={() => {}}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Ver fuente 1/ }));
    expect(onCite).toHaveBeenCalled();
  });

  it("el pie ofrece reabrir las fuentes del mensaje", async () => {
    const onShowSources = vi.fn();
    render(
      <Message
        message={assistant({ content: "ok [1]", sources: [source(1)] })}
        onCite={() => {}}
        onShowSources={onShowSources}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Fuentes \(1\)/ }));
    expect(onShowSources).toHaveBeenCalledWith([expect.objectContaining({ n: 1 })]);
  });

  it("convierte [n] en un chip clickeable cuando la fuente existe", async () => {
    const onCite = vi.fn();
    render(
      <Message
        message={assistant({ content: "La potencia es 10 MW [1].", sources: [source(1)] })}
        onCite={onCite}
        onShowSources={() => {}}
      />,
    );
    const chip = screen.getByRole("button", { name: /Ver fuente 1/ });
    await userEvent.click(chip);
    // Segundo argumento: las fuentes del propio mensaje (el panel muestra
    // las de la respuesta citada, no las de la última pregunta).
    expect(onCite).toHaveBeenCalledWith(
      expect.objectContaining({ n: 1 }),
      expect.arrayContaining([expect.objectContaining({ n: 1 })]),
    );
  });

  it("deja [n] como texto cuando la cita no tiene fuente correspondiente", () => {
    const { container } = render(
      <Message
        message={assistant({ content: "Ver [7] para detalle.", sources: [source(1)] })}
        onCite={() => {}} onShowSources={() => {}}
      />,
    );
    expect(container.querySelectorAll("button.citation-chip")).toHaveLength(0);
    expect(container.textContent).toContain("[7]");
  });

  it("renderiza múltiples citas distintas", () => {
    render(
      <Message
        message={assistant({ content: "[1] y también [2].", sources: [source(1), source(2)] })}
        onCite={() => {}} onShowSources={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /Ver fuente 1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ver fuente 2/ })).toBeInTheDocument();
  });

  it("muestra el estado del agente mientras no hay contenido", () => {
    render(
      <Message
        message={assistant({ streaming: true, status: "Buscando en los contratos" })}
        onCite={() => {}} onShowSources={() => {}}
      />,
    );
    expect(screen.getByText(/Buscando en los contratos/)).toBeInTheDocument();
  });

  it("oculta el estado en cuanto llegan tokens", () => {
    render(
      <Message
        message={assistant({ streaming: true, status: "Redactando", content: "Ya hay texto" })}
        onCite={() => {}} onShowSources={() => {}}
      />,
    );
    expect(screen.queryByText(/Redactando…/)).not.toBeInTheDocument();
  });

  it("cuenta las afirmaciones contrastadas cuando todas se sustentan", () => {
    render(
      <Message
        message={assistant({ content: "ok", grounded: true, claimsTotal: 7, claimsOk: 7 })}
        onCite={() => {}}
        onShowSources={() => {}}
      />,
    );
    expect(screen.getByText(/7 afirmaciones contrastadas/)).toBeInTheDocument();
  });

  it("detalla qué afirmación contradice la fuente citada", async () => {
    render(
      <Message
        message={assistant({
          content: "ok",
          grounded: false,
          claimsTotal: 3,
          claimsOk: 2,
          claimIssues: [
            {
              texto: "la potencia contratada con Pluz en 2026 es 4.5 MW",
              estado: "refutada",
              motivo: "esa tabla es del contrato con Celepsa",
            },
          ],
        })}
        onCite={() => {}}
        onShowSources={() => {}}
      />,
    );
    expect(screen.getByText(/1 de 3 sin sustento/)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/Qué no se pudo verificar/));
    expect(screen.getByText(/Contradice la fuente/)).toBeInTheDocument();
    expect(screen.getByText(/esa tabla es del contrato con Celepsa/)).toBeInTheDocument();
  });

  it("una afirmación sin cita se reporta como tal, no como verificada", () => {
    render(
      <Message
        message={assistant({
          content: "ok",
          grounded: null,
          claimsTotal: 1,
          claimsOk: 0,
          claimIssues: [{ texto: "algo", estado: "sin_cita", motivo: "" }],
        })}
        onCite={() => {}}
        onShowSources={() => {}}
      />,
    );
    expect(screen.getByText(/Sin cita/)).toBeInTheDocument();
    expect(screen.queryByText(/afirmaciones contrastadas/)).not.toBeInTheDocument();
  });

  it("sin verificación no promete nada", () => {
    render(<Message message={assistant({ content: "ok", grounded: null })} onCite={() => {}} onShowSources={() => {}} />);
    expect(screen.queryByText(/contrastada/)).not.toBeInTheDocument();
    expect(screen.queryByText(/sin sustento/)).not.toBeInTheDocument();
  });

  it("muestra el error y oculta el pie cuando falla", () => {
    render(
      <Message
        message={assistant({ content: "parcial", grounded: true, error: "timeout" })}
        onCite={() => {}} onShowSources={() => {}}
      />,
    );
    expect(screen.getByText(/timeout/)).toBeInTheDocument();
    expect(screen.queryByText(/Verificado contra fuentes/)).not.toBeInTheDocument();
  });

  it("marca la ausencia de contexto", () => {
    render(<Message message={assistant({ content: "n/a", noContext: true })} onCite={() => {}} onShowSources={() => {}} />);
    expect(screen.getByText(/Sin contexto en el índice/)).toBeInTheDocument();
  });
});
