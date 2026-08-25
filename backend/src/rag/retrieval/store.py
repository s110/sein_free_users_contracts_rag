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
        if k in ("usuario_libre", "suministrador"):
            # Razón social: match por palabras sobre el espejo normalizado —
            # el usuario escribe "lavanderia landeo" u "orygen", no la
            # denominación exacta con tildes y "S.A.C.".
            conditions.append(
                models.FieldCondition(
                    key=f"{k}_norm",
                    match=models.MatchText(text=normalize_text_filter(str(v))),
                )
            )
        elif k == "doc_id" and isinstance(v, list):
            # El selector temporal multi-documento acota a N docs a la vez
            conditions.append(
                models.FieldCondition(key=k, match=models.MatchAny(any=[str(x) for x in v]))
            )
        elif k == "tipo":
            # El índice histórico mezcla "Contrato" y "contrato" (state del
            # scraper vs parser del filename). MatchAny cubre ambas grafías
            # hasta el próximo reindex completo.
            val = str(v).lower()
            conditions.append(
                models.FieldCondition(key=k, match=models.MatchAny(any=[val, val.capitalize()]))
            )
        else:
            conditions.append(models.FieldCondition(key=k, match=models.MatchValue(value=str(v))))
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
        # Ni la query ni los valores de los filtros: la query conserva "nombres
        # de empresas, RUCs y fechas tal cual" por diseño del ANALYZE_PROMPT y
        # cae a la pregunta literal del usuario cuando el analizador no
        # devuelve nada, así que esta línea reconstruía en `docker logs` el
        # registro de quién preguntó por qué empresa que `agent.analyze`
        # evita a propósito. Se loguean las claves, que bastan para depurar.
        log.info(
            "search: filtros=%s → dense=%d lexical=%d fused=%d",
            sorted(filters or {}),
            len(dense),
            len(lexical),
            len(results),
        )
        return results

    def search_diverse(
        self,
        query: str,
        *,
        n_docs: int,
        per_doc: int = 2,
        filters: dict | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieval con diversidad por documento.

        "Dame 5 contratos de Orygen" no se responde con el top-k plano: los 6
        mejores fragmentos suelen venir del mismo contrato (es el más parecido
        a la pregunta) y el generador concluía que "solo existe un contrato".
        Aquí la selección es voraz sobre el ranking fusionado: acepta como
        máximo `per_doc` fragmentos por doc_id y abre documentos nuevos hasta
        cubrir `n_docs` distintos.
        """
        qfilter = _build_filter(filters)
        want = n_docs * per_doc
        pool = max(self.dense_candidates, want * 4)
        dense = self._dense_search(query, qfilter, limit=pool)
        lexical = self._text_search(query, qfilter, limit=pool)

        by_id = {c.chunk_id: c for c in dense}
        by_id.update({c.chunk_id: c for c in lexical if c.chunk_id not in by_id})
        fused = rrf_fuse([[c.chunk_id for c in dense], [c.chunk_id for c in lexical]])
        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)

        per_doc_count: dict[str, int] = {}
        results: list[RetrievedChunk] = []
        for chunk_id, score in ranked:
            chunk = by_id[chunk_id]
            taken = per_doc_count.get(chunk.doc_id, 0)
            if taken >= per_doc:
                continue
            if chunk.doc_id not in per_doc_count and len(per_doc_count) >= n_docs:
                continue
            chunk.score = round(score, 5)
            per_doc_count[chunk.doc_id] = taken + 1
            results.append(chunk)
            if len(results) >= want:
                break
        # Solo las claves de los filtros; ver el comentario en `search`.
        log.info(
            "search_diverse: filtros=%s → %d fragmentos de %d docs (pedidos %d)",
            sorted(filters or {}),
            len(results),
            len(per_doc_count),
            n_docs,
        )
        return results

    def count_distinct_docs(self, filters: dict | None = None) -> int:
        """Documentos distintos que coinciden con los filtros (solo payload)."""
        qfilter = _build_filter(filters)
        seen: set[str] = set()
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=qfilter,
                with_payload=["doc_id"],
                with_vectors=False,
                limit=1024,
                offset=offset,
            )
            for p in points:
                doc_id = (p.payload or {}).get("doc_id")
                if doc_id:
                    seen.add(doc_id)
            if offset is None:
                return len(seen)

    def _dense_search(
        self, query: str, qfilter: models.Filter | None, limit: int | None = None
    ) -> list[RetrievedChunk]:
        vector = self.embedder.embed_one(query)
        hits = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=qfilter,
            limit=limit or self.dense_candidates,
            with_payload=True,
        ).points
        return [_to_chunk(h.id, h.payload or {}, h.score) for h in hits]

    def _text_search(
        self, query: str, qfilter: models.Filter | None, limit: int | None = None
    ) -> list[RetrievedChunk]:
        """Match léxico vía índice full-text; el orden es de inserción, la RRF lo pondera."""
        text_condition = models.FieldCondition(key="text", match=models.MatchText(text=query))
        must = [text_condition]
        if qfilter and qfilter.must:
            must.extend(qfilter.must)  # type: ignore[arg-type]
        try:
            points, _ = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=models.Filter(must=must),
                limit=limit or self.text_candidates,
                with_payload=True,
            )
        except Exception as e:  # noqa: BLE001 — léxico es best-effort, densa siempre corre
            log.warning("Búsqueda léxica falló (%s); solo densa", e)
            return []
        return [_to_chunk(p.id, p.payload or {}, 0.0) for p in points]

    def count(self) -> int:
        return self.client.count(self.collection, exact=True).count

    def find_extreme_doc(self, filters: dict | None, *, latest: bool = True) -> dict | None:
        """El documento con fecha de suscripción máxima (o mínima). Ver find_extreme_docs."""
        docs = self.find_extreme_docs(filters, latest=latest, n=1)
        return docs[0] if docs else None

    def find_extreme_docs(
        self, filters: dict | None, *, latest: bool = True, n: int = 1
    ) -> list[dict]:
        """Los N documentos con fecha de suscripción máxima (o mínima) del índice.

        "¿El contrato más reciente?" no es respondible por similitud semántica:
        el retrieval trae fragmentos parecidos a la pregunta, no el máximo de
        un campo. Esto escanea la metadata (solo payload, sin vectores) y
        resuelve el superlativo de verdad. Respeta los filtros activos, así
        "la adenda más reciente de X" y "los 3 contratos más nuevos" funcionan.
        """
        qfilter = _build_filter(filters)
        by_doc: dict[str, dict] = {}
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=qfilter,
                with_payload=[
                    "doc_id",
                    "source_file",
                    "tipo",
                    "usuario_libre",
                    "suministrador",
                    "fecha_suscripcion",
                ],
                with_vectors=False,
                limit=1024,
                offset=offset,
            )
            for pt in points:
                payload = pt.payload or {}
                doc_id, fecha = payload.get("doc_id"), payload.get("fecha_suscripcion")
                if doc_id and fecha:
                    by_doc.setdefault(doc_id, payload)
            if offset is None:
                break
        ordered = sorted(by_doc.values(), key=lambda p: p["fecha_suscripcion"], reverse=latest)
        return ordered[:n]

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
