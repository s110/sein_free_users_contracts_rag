# SEIN Free Users Contracts RAG

RAG **agéntico, 100% local y open source** para consultar con fuentes los
contratos de suministro eléctrico de usuarios libres del SEIN (Osinergmin).
Consume los Markdown que produce
[s110/ocr_pdf_markdown](https://github.com/s110/ocr_pdf_markdown) y responde
preguntas citando documento, sección y página.

```
                    ┌───────────────────────── Mac Mini M4 (16GB) ─────────────────────────┐
 PDFs → OCR         │                                                                      │
 (ocr_pdf_markdown) │   vault .md ──▶ ingesta incremental ──▶ Qdrant (denso + full-text)   │
        │           │   (frontmatter)  (idempotente por hash)        ▲                     │
        ▼           │                                                │ retrieval híbrido   │
   vault Obsidian ──┼──▶ ┌──────────────────────────────────────────┴────────────┐        │
                    │    │  Agente LangGraph: analyze → retrieve → grade          │        │
                    │    │   ↺ rewrite (si nada relevante) → generate → verify    │        │
                    │    └──────────────────────┬──────────────────────────────  ─┘        │
                    │        Ollama (nativo):   │ SSE streaming + citas [n]                │
                    │        qwen3.5:4b + qwen3-embedding  ▼                                          │
                    │    FastAPI ◀── nginx ◀── React+TS (chat, fuentes, filtros)           │
                    └────────────────────────────│─────────────────────────────────────────┘
                                                 ▼
                                  Cloudflare Tunnel (usuarios externos)
```

## Stack (todo open source)

| Capa | Tecnología | Por qué |
|---|---|---|
| LLM | [Qwen3.5 4B](https://ollama.com/library/qwen3.5) vía Ollama | Sucesor de Qwen3 4B (mar 2026): 201 idiomas, 256K de contexto, mismo footprint |
| Embeddings | [Qwen3-Embedding 0.6B](https://ollama.com/library/qwen3-embedding) vía Ollama | Multilingüe (100+ idiomas), mejor que bge-m3 en MTEB con la mitad de RAM (~0.6GB) |
| Vector store | [Qdrant](https://qdrant.tech) | Filtros de payload + índice full-text → búsqueda híbrida |
| Orquestación | [LangGraph](https://langchain-ai.github.io/langgraph/) | Grafo agéntico explícito con ciclos controlados |
| API | FastAPI + SSE | Streaming de tokens y estados del agente |
| Frontend | React + TypeScript (Vite) | Chat con citas clicables y panel de fuentes |
| Exposición | Cloudflare Tunnel | HTTPS público sin abrir puertos del router |

## Por qué es confiable

- **Respuestas con fuentes**: cada afirmación lleva una cita `[n]` que mapea a
  documento + página + sección; el frontend las muestra con snippet y link al
  PDF original de Osinergmin.
- **Grading de relevancia**: un evaluador LLM descarta fragmentos recuperados
  que no aportan; si nada es relevante, el agente **reformula la búsqueda**
  (hasta 2 veces) antes de rendirse con una respuesta honesta de "no está en
  el índice" — nunca inventa.
- **Verificación de groundedness**: tras generar, otro paso verifica que la
  respuesta esté sustentada en el contexto; el frontend marca
  "✓ Verificado contra fuentes" o "⚠ no concluyente".
- **Ingesta idempotente por `source_hash`** (mismo contrato que el pipeline
  OCR): reingestar el vault completo cuesta ~0 si nada cambió; documentos
  modificados se reindexan atómicamente (delete + upsert con IDs
  determinísticos) y los borrados del vault se purgan del índice.
- **Búsqueda híbrida**: densa (semántica) + léxica (full-text multilingüe)
  fusionadas con RRF — los RUCs, códigos y fechas exactas no se pierden en el
  embedding.
- **Manifest JSONL de ingesta** + logs estructurados JSON + healthchecks en
  todos los contenedores.

## Quickstart (Mac Mini)

```bash
# 1. Modelos locales (Ollama nativo en el host, por acceso a Metal)
brew install ollama && brew services start ollama
make models                       # qwen3.5:4b + qwen3-embedding:0.6b

# 2. Configuración
cp .env.example .env              # ajusta VAULT_DIR a tu vault de .md

# 3. Stack
make up                           # qdrant + backend + frontend en Docker

# 4. Ingesta (idempotente — corre las veces que quieras)
make ingest

# 5. Abre http://localhost:8080
```

### Flujo completo desde PDFs

```bash
# Repo ocr_pdf_markdown: PDFs → Markdown con frontmatter
uv run ocr-pipeline run ~/Contratos_osinergmin/pdfs --vault ~/Obsidian/Osinergmin

# Este repo: Markdown → índice vectorial
VAULT_DIR=~/Obsidian/Osinergmin make ingest
```

La ingesta se puede automatizar con launchd/cron: al ser idempotente por
hash, correrla cada 30 min tras el OCR solo procesa lo nuevo.

## Uso para usuarios externos

Ver **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**: exposición HTTPS con
Cloudflare Tunnel (sin abrir puertos), autenticación por API key, rate
limiting y arranque automático tras reinicios del Mac mini.

TL;DR:

```bash
# .env: define RAG_API_KEY y CLOUDFLARE_TUNNEL_TOKEN
docker compose --profile public up -d
```

## API

| Endpoint | Descripción |
|---|---|
| `POST /api/chat` | Chat SSE: eventos `status`, `sources`, `token`, `end` |
| `GET /api/health` | Estado de Qdrant, Ollama y modelos |
| `GET /api/documents` | Documentos indexados (para auditoría) |
| `GET /api/meta` | Versiones de app y prompts, modelos activos |

Con `RAG_API_KEY` definida, `chat` y `documents` exigen header `X-API-Key`.
Con `RAG_PUBLIC_CHAT=true`, `chat` acepta además visitantes sin clave con una
cuota diaria por IP (`RAG_CHAT_DAILY_LIMIT`, default 5); `documents` y `meta`
siguen exigiendo la clave porque publican RUC de usuarios libres.

## Desarrollo

```bash
make setup          # uv sync + npm install
make lint           # ruff + tsc
make test           # pytest (sin servicios: unit tests puros)
make dev-backend    # uvicorn --reload en :8000
make dev-frontend   # vite en :5173 (proxy /api → :8000)
make eval           # evaluación contra golden set (stack vivo)
```

CI (GitHub Actions): lint + tests + typecheck + build de ambas imágenes.

```
backend/src/rag/
├── config.py            # settings 12-factor (RAG_*)
├── llm.py               # factoría ChatOllama
├── schemas.py           # contratos de datos (frontmatter → payload → API)
├── ingestion/
│   ├── loader.py        # .md + frontmatter YAML del pipeline OCR
│   ├── chunker.py       # chunking markdown-aware con tracking de páginas
│   ├── embedder.py      # Ollama /api/embed con batching + retries
│   ├── indexer.py       # Qdrant incremental por source_hash + manifest
│   └── cli.py           # sein-rag-ingest
├── retrieval/store.py   # híbrido denso + full-text con fusión RRF
├── graph/
│   ├── agent.py         # grafo LangGraph (nodos + aristas condicionales)
│   ├── prompts.py       # prompts versionados (PROMPT_VERSION)
│   └── state.py
└── api/main.py          # FastAPI + SSE
frontend/src/            # React + TS: chat streaming, citas, filtros
backend/eval/            # golden set + harness de evaluación
```

## Evaluación (AI engineering)

`backend/eval/golden.jsonl` define preguntas con su documento esperado.
`make eval` corre el agente completo y reporta **retrieval hit-rate** y
**grounded rate**, guardando cada corrida en `eval/results/` con la versión
de prompts y modelos usados — corre esto antes de desplegar cambios de
prompt, modelo o chunking y compara contra la corrida anterior.
