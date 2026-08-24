export interface SourceRef {
  n: number;
  source_file: string;
  doc_id: string;
  section: string | null;
  page_start: number | null;
  page_end: number | null;
  usuario_libre: string | null;
  suministrador: string | null;
  fecha_suscripcion: string | null;
  tipo: string | null;
  source_url: string | null;
  snippet: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  sources?: SourceRef[];
  grounded?: boolean | null;
  noContext?: boolean;
  status?: string; // paso actual del agente mientras genera
  streaming?: boolean;
  error?: string;
}

export interface Filters {
  tipo?: string;
  ruc_usuario_libre?: string;
  usuario_libre?: string;
}

export type SseEvent =
  | { type: "status"; data: { step: string; detail: string } }
  | { type: "sources"; data: { sources: SourceRef[] } }
  | { type: "token"; data: { text: string } }
  | {
      type: "end";
      data: {
        answer: string;
        grounded: boolean | null;
        no_context: boolean;
        rewrites: number;
        sources: SourceRef[];
      };
    }
  | { type: "error"; data: { message: string } };

export interface HealthInfo {
  status: string;
  version: string;
  llm: string;
  embeddings: string;
  indexed_chunks?: number;
  qdrant?: string;
  ollama?: string;
  missing_models?: string[];
}

export interface IndexedDocument {
  doc_id: string;
  source_file: string;
  tipo: string | null;
  suministrador: string | null;
  usuario_libre: string | null;
  ruc_usuario_libre: string | null;
  fecha_suscripcion: string | null;
  source_url: string | null;
}
