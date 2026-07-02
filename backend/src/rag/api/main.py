"""API HTTP del RAG: chat con streaming SSE + endpoints de observabilidad.

Eventos SSE de /api/chat:
- status  {step, detail}   — progreso del agente (analizando, buscando, ...)
- sources {sources: [...]} — fuentes citables [n] antes de generar
- token   {text}           — tokens de la respuesta en streaming
- end     {answer, grounded, no_context, rewrites}
- error   {message}
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
import orjson
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from qdrant_client import QdrantClient

from .. import __version__
from ..config import Settings, get_settings
from ..graph import prompts
from ..graph.agent import ContractsAgent
from ..ingestion.embedder import OllamaEmbedder
from ..logging_setup import setup_logging
from ..retrieval.store import HybridStore
from ..schemas import ChatRequest, RetrievedChunk, SourceRef

log = logging.getLogger("rag.api")

STEP_LABELS = {
    "analyze": "Analizando la pregunta",
    "retrieve": "Buscando en los contratos",
    "grade": "Evaluando relevancia de fragmentos",
    "rewrite": "Reformulando la búsqueda",
    "generate": "Redactando respuesta",
    "verify": "Verificando fidelidad a las fuentes",
    "no_context": "Sin contexto relevante",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    client = QdrantClient(url=settings.qdrant_url, timeout=60)
    embedder = OllamaEmbedder(
        host=settings.ollama_host,
        model=settings.embedding_model,
        batch_size=settings.embed_batch_size,
    )
    store = HybridStore(
        client,
        embedder,
        settings.collection,
        dense_candidates=settings.dense_candidates,
        text_candidates=settings.text_candidates,
    )
    app.state.settings = settings
    app.state.qdrant = client
    app.state.embedder = embedder
    app.state.store = store
    app.state.agent = ContractsAgent(settings, store)
    log.info(
        "API lista: llm=%s embed=%s collection=%s",
        settings.llm_model,
        settings.embedding_model,
        settings.collection,
    )
    yield
    embedder.close()
    client.close()


app = FastAPI(title="SEIN Free Users Contracts RAG", version=__version__, lifespan=lifespan)

_cors = get_settings().cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors.split(",")] if _cors != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def require_api_key(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if not settings.api_key:
        return
    provided = request.headers.get("x-api-key", "")
    if provided != settings.api_key:
        raise HTTPException(status_code=401, detail="API key inválida o ausente")


def _sse(event_type: str, data: dict) -> str:
    return f"data: {orjson.dumps({'type': event_type, 'data': data}).decode()}\n\n"


def _sources_from(docs: list[RetrievedChunk]) -> list[dict]:
    return [
        SourceRef(
            n=i,
            source_file=d.source_file,
            doc_id=d.doc_id,
            section=d.section,
            page_start=d.page_start,
            page_end=d.page_end,
            usuario_libre=d.usuario_libre,
            suministrador=d.suministrador,
            fecha_suscripcion=d.fecha_suscripcion,
            tipo=d.tipo,
            source_url=d.source_url,
            snippet=d.text[:300],
        ).model_dump()
        for i, d in enumerate(docs, start=1)
    ]


@app.get("/api/health")
async def health(request: Request):
    settings: Settings = request.app.state.settings
    out = {
        "status": "ok",
        "version": __version__,
        "llm": settings.llm_model,
        "embeddings": settings.embedding_model,
    }
    try:
        out["indexed_chunks"] = request.app.state.store.count()
        out["qdrant"] = "ok"
    except Exception as e:  # noqa: BLE001
        out["qdrant"] = f"error: {e}"
        out["status"] = "degraded"
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{settings.ollama_host}/api/tags")
            r.raise_for_status()
            available = {m["name"] for m in r.json().get("models", [])}
            out["ollama"] = "ok"
            missing = [
                m
                for m in (settings.llm_model, settings.embedding_model)
                if m not in available and f"{m}:latest" not in available
            ]
            if missing:
                out["missing_models"] = missing
                out["status"] = "degraded"
    except Exception as e:  # noqa: BLE001
        out["ollama"] = f"error: {e}"
        out["status"] = "degraded"
    return out


@app.get("/api/documents", dependencies=[Depends(require_api_key)])
async def documents(request: Request):
    try:
        docs = request.app.state.store.list_documents()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Qdrant no disponible: {e}") from e
    return {"count": len(docs), "documents": docs}


@app.get("/api/meta")
async def meta(request: Request):
    settings: Settings = request.app.state.settings
    return {
        "version": __version__,
        "prompt_version": prompts.PROMPT_VERSION,
        "llm": settings.llm_model,
        "embeddings": settings.embedding_model,
        "collection": settings.collection,
        "auth_required": bool(settings.api_key),
    }


@app.post("/api/chat", dependencies=[Depends(require_api_key)])
async def chat(request: Request, body: ChatRequest):
    agent: ContractsAgent = request.app.state.agent

    async def event_stream():
        state_in = {
            "question": body.question,
            "history": body.history,
            "user_filters": body.filters or None,
            "rewrites": 0,
        }
        final: dict = {}
        sources_sent = False
        try:
            async for ev in agent.graph.astream_events(state_in, version="v2"):
                kind = ev["event"]
                name = ev.get("name", "")
                if kind == "on_chain_start" and name in STEP_LABELS:
                    yield _sse("status", {"step": name, "detail": STEP_LABELS[name]})
                elif kind == "on_chain_end" and name in STEP_LABELS:
                    output = ev.get("data", {}).get("output") or {}
                    if isinstance(output, dict):
                        final.update(output)
                    if name == "grade" and not sources_sent:
                        docs = final.get("relevant_documents") or []
                        if docs:
                            yield _sse("sources", {"sources": _sources_from(docs)})
                            sources_sent = True
                elif (
                    kind == "on_chat_model_stream"
                    and ev.get("metadata", {}).get("langgraph_node") == "generate"
                ):
                    chunk = ev.get("data", {}).get("chunk")
                    text = getattr(chunk, "content", "")
                    if text:
                        yield _sse("token", {"text": text})
            yield _sse(
                "end",
                {
                    "answer": final.get("answer", ""),
                    "grounded": final.get("grounded"),
                    "no_context": bool(final.get("no_context")),
                    "rewrites": final.get("rewrites", 0),
                    "sources": _sources_from(final.get("relevant_documents") or []),
                },
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Error en /api/chat")
            yield _sse("error", {"message": f"Error interno del agente: {e}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
