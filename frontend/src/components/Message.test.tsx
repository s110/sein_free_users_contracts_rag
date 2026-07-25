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
      <Message message={{ role: "user", content: "¿Cuál es la potencia?" }} onCite={() => {}} />,
    );
    expect(screen.getByText("¿Cuál es la potencia?")).toBeInTheDocument();
  });

  it("no interpreta HTML del modelo (sin XSS)", () => {
    const { container } = render(
      <Message
        message={assistant({ content: "<img src=x onerror=alert(1)>" })}
        onCite={() => {}}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("convierte [n] en un chip clickeable cuando la fuente existe", async () => {
    const onCite = vi.fn();
    render(
      <Message
        message={assistant({ content: "La potencia es 10 MW [1].", sources: [source(1)] })}
        onCite={onCite}
      />,
    );
    const chip = screen.getByRole("button", { name: "1" });
    await userEvent.click(chip);
    expect(onCite).toHaveBeenCalledWith(expect.objectContaining({ n: 1 }));
  });

  it("deja [n] como texto cuando la cita no tiene fuente correspondiente", () => {
    const { container } = render(
      <Message
        message={assistant({ content: "Ver [7] para detalle.", sources: [source(1)] })}
        onCite={() => {}}
      />,
    );
    expect(container.querySelectorAll("button.citation-chip")).toHaveLength(0);
    expect(container.textContent).toContain("[7]");
  });

  it("renderiza múltiples citas distintas", () => {
    render(
      <Message
        message={assistant({ content: "[1] y también [2].", sources: [source(1), source(2)] })}
        onCite={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "2" })).toBeInTheDocument();
  });

  it("muestra el estado del agente mientras no hay contenido", () => {
    render(
      <Message
        message={assistant({ streaming: true, status: "Buscando en los contratos" })}
        onCite={() => {}}
      />,
    );
    expect(screen.getByText(/Buscando en los contratos/)).toBeInTheDocument();
  });

  it("oculta el estado en cuanto llegan tokens", () => {
    render(
      <Message
        message={assistant({ streaming: true, status: "Redactando", content: "Ya hay texto" })}
        onCite={() => {}}
      />,
    );
    expect(screen.queryByText(/Redactando…/)).not.toBeInTheDocument();
  });

  it("muestra el badge verificado cuando grounded es true", () => {
    render(<Message message={assistant({ content: "ok", grounded: true })} onCite={() => {}} />);
    expect(screen.getByText(/Verificado contra fuentes/)).toBeInTheDocument();
  });

  it("muestra la advertencia cuando grounded es false", () => {
    render(<Message message={assistant({ content: "ok", grounded: false })} onCite={() => {}} />);
    expect(screen.getByText(/Verificación no concluyente/)).toBeInTheDocument();
  });

  it("no muestra badges cuando grounded es null", () => {
    render(<Message message={assistant({ content: "ok", grounded: null })} onCite={() => {}} />);
    expect(screen.queryByText(/Verificado contra fuentes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Verificación no concluyente/)).not.toBeInTheDocument();
  });

  it("muestra el error y oculta el pie cuando falla", () => {
    render(
      <Message
        message={assistant({ content: "parcial", grounded: true, error: "timeout" })}
        onCite={() => {}}
      />,
    );
    expect(screen.getByText(/timeout/)).toBeInTheDocument();
    expect(screen.queryByText(/Verificado contra fuentes/)).not.toBeInTheDocument();
  });

  it("marca la ausencia de contexto", () => {
    render(<Message message={assistant({ content: "n/a", noContext: true })} onCite={() => {}} />);
    expect(screen.getByText(/Sin contexto en el índice/)).toBeInTheDocument();
  });
});
