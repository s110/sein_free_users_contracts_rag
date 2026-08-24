"""Retrieval híbrido sobre Qdrant: denso (embedder Ollama) + léxico (full-text) con RRF.

Los contratos eléctricos mezclan lenguaje natural ("potencia contratada")
con identificadores exactos (RUC, códigos de suministrador, fechas). La
búsqueda densa cubre lo semántico; el índice full-text rescata matches
exactos que un embedding diluye. Reciprocal Rank Fusion combina ambos sin
necesidad de calibrar scores entre espacios distintos.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from qdrant_client import QdrantClient, models

from ..ingestion.embedder import OllamaEmbedder
from ..schemas import RetrievedChunk, normalize_text_filter

log = logging.getLogger("rag.retrieval")

# Filtros de metadata que el agente puede aplicar
FILTERABLE_FIELDS = {
    "tipo",
    "suministrador",
    "suministrador_code",
    "usuario_libre",
    "ruc_usuario_libre",
    "fecha_suscripcion",
    "source_file",
    "doc_id",
}

RRF_K = 60


def _build_filter(filters: dict | None) -> models.Filter | None:
    if not filters:
        return None
    conditions = []
    for k, v in filters.items():
        if k not in FILTERABLE_FIELDS or not v:
            continue
        if k == "usuario_libre":
            # Razón social: match por palabras sobre el espejo normalizado —
            # el usuario escribe "lavanderia landeo", no la denominación
            # exacta con tildes y "S.A.C.".
            conditions.append(
                models.FieldCondition(
                    key="usuario_libre_norm",
                    match=models.MatchText(text=normalize_text_filter(str(v))),
                )
            )
        else:
            conditions.append(
                models.FieldCondition(key=k, match=models.MatchValue(value=str(v)))
            )
    return models.Filter(must=conditions) if conditions else None


def _to_chunk(point_id: str, payload: dict, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(point_id),
        doc_id=payload.get("doc_id", ""),
        text=payload.get("text", ""),
        score=score,
        section=payload.get("section"),
        page_start=payload.get("page_start"),
        page_end=payload.get("page_end"),
        source_file=payload.get("source_file", ""),
        tipo=payload.get("tipo"),
        suministrador=payload.get("suministrador"),
        usuario_libre=payload.get("usuario_libre"),
        ruc_usuario_libre=payload.get("ruc_usuario_libre"),
        fecha_suscripcion=payload.get("fecha_suscripcion"),
        source_url=payload.get("source_url"),
    )


def rrf_fuse(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion: score(d) = Σ 1/(k + rank_i(d))."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking):
            scores[chunk_id] += 1.0 / (k + rank + 1)
    return dict(scores)


class HybridStore:
    def __init__(
        self,
        client: QdrantClient,
        embedder: OllamaEmbedder,
        collection: str,
        dense_candidates: int = 20,
        text_candidates: int = 20,
    ) -> None:
        self.client = client
        self.embedder = embedder
        self.collection = collection
        self.dense_candidates = dense_candidates
        self.text_candidates = text_candidates

    def search(
        self, query: str, top_k: int = 6, filters: dict | None = None
    ) -> list[RetrievedChunk]:
        qfilter = _build_filter(filters)
        dense = self._dense_search(query, qfilter)
        lexical = self._text_search(query, qfilter)

        by_id = {c.chunk_id: c for c in dense}
        by_id.update({c.chunk_id: c for c in lexical if c.chunk_id not in by_id})

        fused = rrf_fuse([[c.chunk_id for c in dense], [c.chunk_id for c in lexical]])
        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]

        results = []
        for chunk_id, score in ranked:
            chunk = by_id[chunk_id]
            chunk.score = round(score, 5)
            results.append(chunk)
        log.info(
            "search '%s' filtros=%s → dense=%d lexical=%d fused=%d",
            query[:80],
            filters,
            len(dense),
            len(lexical),
            len(results),
        )
        return results

    def _dense_search(self, query: str, qfilter: models.Filter | None) -> list[RetrievedChunk]:
        vector = self.embedder.embed_one(query)
        hits = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=qfilter,
            limit=self.dense_candidates,
            with_payload=True,
        ).points
        return [_to_chunk(h.id, h.payload or {}, h.score) for h in hits]

    def _text_search(self, query: str, qfilter: models.Filter | None) -> list[RetrievedChunk]:
        """Match léxico vía índice full-text; el orden es de inserción, la RRF lo pondera."""
        text_condition = models.FieldCondition(key="text", match=models.MatchText(text=query))
        must = [text_condition]
        if qfilter and qfilter.must:
            must.extend(qfilter.must)  # type: ignore[arg-type]
        try:
            points, _ = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=models.Filter(must=must),
                limit=self.text_candidates,
                with_payload=True,
            )
        except Exception as e:  # noqa: BLE001 — léxico es best-effort, densa siempre corre
            log.warning("Búsqueda léxica falló (%s); solo densa", e)
            return []
        return [_to_chunk(p.id, p.payload or {}, 0.0) for p in points]

    def count(self) -> int:
        return self.client.count(self.collection, exact=True).count

    def list_documents(self, limit: int = 500) -> list[dict]:
        """Documentos únicos indexados (para el panel de fuentes del frontend)."""
        docs: dict[str, dict] = {}
        offset = None
        while len(docs) < limit:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                with_payload=[
                    "doc_id",
                    "source_file",
                    "tipo",
                    "suministrador",
                    "usuario_libre",
                    "ruc_usuario_libre",
                    "fecha_suscripcion",
                    "source_url",
                ],
                with_vectors=False,
                limit=512,
                offset=offset,
            )
            for p in points:
                payload = p.payload or {}
                doc_id = payload.get("doc_id")
                if doc_id and doc_id not in docs:
                    docs[doc_id] = payload
            if offset is None:
                break
        return sorted(docs.values(), key=lambda d: d.get("doc_id", ""))
