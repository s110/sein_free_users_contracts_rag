"""API HTTP del RAG: chat con streaming SSE + endpoints de observabilidad.

Eventos SSE de /api/chat:
- status  {step, detail}   — progreso del agente (analizando, buscando, ...)
- sources {sources: [...]} — fuentes citables [n] antes de generar
- token   {text}           — tokens de la respuesta en streaming
- end     {answer, grounded, no_context, rewrites}
- error   {message}
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from contextlib import asynccontextmanager

import httpx
import orjson
from fastapi import Depends, FastAPI, HTTPException, Request, Response
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
from .quota import DailyQuota

log = logging.getLogger("rag.api")

STEP_LABELS = {
    "analyze": "Analizando la pregunta",
    "retrieve": "Buscando en los contratos",
    "grade": "Evaluando relevancia de fragmentos",
    "rewrite": "Reformulando la búsqueda",
    "generate": "Redactando respuesta",
    "verify": "Verificando fidelidad a las fuentes",
    "no_context": "Sin contexto relevante",
    "refuse": "Consulta fuera del alcance del asistente",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    # Falla al arrancar, no en la primera petición: una API sin clave y sin
    # RAG_ALLOW_ANONYMOUS no debe llegar nunca a escuchar en un puerto.
    settings.validate_runtime()
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
    _configure_cors(app, settings)
    app.state.settings = settings
    app.state.qdrant = client
    app.state.embedder = embedder
    app.state.store = store
    app.state.agent = ContractsAgent(settings, store)
    app.state.quota = (
        DailyQuota(settings.quota_db_path, settings.chat_daily_limit)
        if settings.public_chat
        else None
    )
    log.info(
        "API lista: llm=%s embed=%s collection=%s",
        settings.llm_model,
        settings.embedding_model,
        settings.collection,
    )
    yield
    if app.state.quota is not None:
        app.state.quota.close()
    embedder.close()
    client.close()


def _configure_cors(app: FastAPI, settings: Settings) -> None:
    """CORS según config. Se aplica en el lifespan, no en import time.

    Antes `get_settings()` se llamaba al importar el módulo, lo que congelaba
    la config y calentaba el `lru_cache` antes de que ningún test pudiera
    parchear el entorno — motivo por el que este módulo estaba al 0% de
    cobertura.
    """
    origins = settings.cors_origin_list
    if not origins:
        return  # mismo origen (nginx sirve front y API): no hace falta CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key"],
    )


app = FastAPI(title="SEIN Free Users Contracts RAG", version=__version__, lifespan=lifespan)


def _has_valid_api_key(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    if not settings.api_key:
        return False
    provided = request.headers.get("x-api-key", "")
    # compare_digest: la comparación con `!=` corta en el primer byte distinto
    # y filtra la clave carácter a carácter ante un atacante que mida.
    return secrets.compare_digest(provided, settings.api_key)


async def require_api_key(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if not settings.api_key:
        # Solo se llega aquí si validate_runtime() aceptó allow_anonymous.
        return
    if not _has_valid_api_key(request):
        raise HTTPException(status_code=401, detail="API key inválida o ausente")


def _client_ip(request: Request) -> str:
    settings: Settings = request.app.state.settings
    header_ip = request.headers.get(settings.trusted_ip_header, "").strip()
    if header_ip:
        return header_ip
    return request.client.host if request.client else "desconocida"


async def chat_access(request: Request) -> None:
    """Acceso a /api/chat: clave válida = ilimitado; sin clave, cuota por IP.

    Con RAG_PUBLIC_CHAT=false se comporta exactamente como require_api_key
    (el modo histórico). La cuota vive en el backend y no en nginx porque
    nginx solo sabe limitar ráfagas por minuto; el costo real es el total de
    preguntas del día contra un LLM que atiende en serie.
    """
    settings: Settings = request.app.state.settings
    if _has_valid_api_key(request):
        request.state.quota_remaining = None  # ilimitado
        return
    if not settings.public_chat:
        await require_api_key(request)
        request.state.quota_remaining = None
        return
    quota: DailyQuota = request.app.state.quota
    ip = _client_ip(request)
    allowed, remaining = await asyncio.to_thread(quota.hit, ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Alcanzaste el límite de {settings.chat_daily_limit} preguntas "
                "por día. La cuota se renueva a las 00:00 UTC."
            ),
            headers={"Retry-After": "86400"},
        )
    request.state.quota_remaining = remaining


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
async def health(request: Request, response: Response):
    """Estado del servicio. Devuelve 503 si algo esencial no responde.

    Antes siempre devolvía 200, así que el HEALTHCHECK del contenedor
    (`curl -fsS`) daba OK con Ollama caído: Docker reportaba el backend sano,
    `restart: unless-stopped` no se disparaba nunca, y la primera noticia del
    incidente venía de un usuario.

    Los detalles del fallo van al log, no al cuerpo: este endpoint no
    requiere autenticación y las cadenas de excepción exponían la topología
    interna (nombres DNS de contenedores, puertos, versiones de librerías).
    """
    settings: Settings = request.app.state.settings
    out = {
        "status": "ok",
        "version": __version__,
        "llm": settings.deepseek_model
        if settings.llm_provider == "deepseek"
        else settings.llm_model,
        "embeddings": settings.embedding_model,
    }
    try:
        out["indexed_chunks"] = await asyncio.to_thread(request.app.state.store.count)
        out["qdrant"] = "ok"
    except Exception:  # noqa: BLE001
        log.exception("Health: Qdrant no responde")
        out["qdrant"] = "error"
        out["status"] = "degraded"
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{settings.ollama_host}/api/tags")
            r.raise_for_status()
            available = {m.get("name", "") for m in r.json().get("models", [])}
            out["ollama"] = "ok"
            required = (
                (settings.embedding_model,)
                if settings.llm_provider != "ollama"
                else (settings.llm_model, settings.embedding_model)
            )
            missing = [
                m for m in required if m not in available and f"{m}:latest" not in available
            ]
            if missing:
                out["missing_models"] = missing
                out["status"] = "degraded"
    except Exception:  # noqa: BLE001
        log.exception("Health: Ollama no responde")
        out["ollama"] = "error"
        out["status"] = "degraded"

    if out["status"] != "ok":
        response.status_code = 503
    return out


@app.get("/api/documents", dependencies=[Depends(require_api_key)])
async def documents(request: Request):
    try:
        # list_documents pagina la colección entera de forma síncrona.
        docs = await asyncio.to_thread(request.app.state.store.list_documents)
    except Exception as e:  # noqa: BLE001
        ref = uuid.uuid4().hex[:12]
        log.exception("Fallo listando documentos (ref=%s)", ref)
        raise HTTPException(
            status_code=503, detail=f"Servicio de índice no disponible (ref {ref})"
        ) from e
    return {"count": len(docs), "documents": docs}


@app.get("/api/meta", dependencies=[Depends(require_api_key)])
async def meta(request: Request):
    """Metadatos del despliegue. Detrás de la API key: `auth_required: false`
    le decía a cualquier escáner que /api/chat y /api/documents están abiertos,
    sin necesidad de probar."""
    settings: Settings = request.app.state.settings
    return {
        "version": __version__,
        "prompt_version": prompts.PROMPT_VERSION,
        "llm": settings.llm_model,
        "embeddings": settings.embedding_model,
        "collection": settings.collection,
    }


@app.post("/api/chat", dependencies=[Depends(chat_access)])
async def chat(request: Request, body: ChatRequest):
    agent: ContractsAgent = request.app.state.agent

    async def event_stream():
        state_in = {
            "question": body.question,
            # El grafo trabaja con dicts planos; el modelo pydantic solo
            # existe para acotar y validar lo que entra por HTTP.
            "history": [m.model_dump() for m in body.history],
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
        except Exception:  # noqa: BLE001
            ref = uuid.uuid4().hex[:12]
            log.exception("Error en /api/chat (ref=%s)", ref)
            yield _sse(
                "error",
                {"message": f"Error interno del agente. Referencia para soporte: {ref}"},
            )

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    remaining = getattr(request.state, "quota_remaining", None)
    if remaining is not None:
        # El frontend muestra cuántas preguntas quedan hoy sin otra llamada.
        headers["X-Quota-Remaining"] = str(remaining)
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=headers,
    )
