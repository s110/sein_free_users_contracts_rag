"""Carga de .md producidos por ocr_pdf_markdown: frontmatter YAML + cuerpo.

El contrato de datos con el pipeline OCR es el frontmatter: `source_hash`
(SHA-256 del PDF origen) es la clave de idempotencia para indexado
incremental, y los campos Osinergmin (ruc_usuario_libre, suministrador,
fecha_suscripcion, tipo) se vuelven payload filtrable en Qdrant.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..schemas import DocumentMeta

log = logging.getLogger("rag.ingestion")

SKIP_DIRS = {".ocr", ".obsidian", ".git", ".trash"}


@dataclass
class LoadedDocument:
    doc_id: str  # ruta relativa al vault, sin extensión
    path: Path
    meta: DocumentMeta
    body: str


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Separa frontmatter YAML del cuerpo. Sin frontmatter → ({}, texto)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        data = yaml.safe_load(m.group(1)) or {}
        if not isinstance(data, dict):
            return {}, text
    except yaml.YAMLError:
        log.warning("Frontmatter inválido, se ingesta sin metadata")
        return {}, text
    return data, m.group(2).lstrip("\n")


def load_document(path: Path, vault_root: Path) -> LoadedDocument | None:
    """Carga un .md; None si no tiene el mínimo (source_hash) para ingesta confiable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.error("No se pudo leer %s: %s", path, e)
        return None

    fm, body = split_frontmatter(text)
    if not body.strip():
        log.warning("Documento vacío, se salta: %s", path)
        return None

    source_hash = str(fm.get("source_hash") or "")
    if not source_hash:
        # Sin hash no hay idempotencia; usamos hash del contenido como fallback
        import hashlib

        source_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        log.info("Sin source_hash en frontmatter, fallback a hash de contenido: %s", path.name)

    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]

    meta = DocumentMeta(
        source_file=str(fm.get("source_file") or path.name),
        source_hash=source_hash,
        created=_opt_str(fm.get("created")),
        pages=_opt_int(fm.get("pages")),
        tipo=_opt_str(fm.get("tipo")),
        suministrador=_opt_str(fm.get("suministrador")),
        suministrador_code=_opt_str(fm.get("suministrador_code")),
        usuario_libre=_opt_str(fm.get("usuario_libre")),
        ruc_usuario_libre=_opt_str(fm.get("ruc_usuario_libre")),
        fecha_suscripcion=_opt_str(fm.get("fecha_suscripcion")),
        source_url=_opt_str(fm.get("source_url")),
        tags=[str(t) for t in tags],
    )
    doc_id = str(path.relative_to(vault_root).with_suffix(""))
    return LoadedDocument(doc_id=doc_id, path=path, meta=meta, body=body)


def iter_vault(vault_root: Path) -> list[Path]:
    """Lista .md del vault, ignorando directorios de sistema, orden estable."""
    files = [
        p
        for p in sorted(vault_root.rglob("*.md"))
        if not any(part in SKIP_DIRS for part in p.parts)
    ]
    return files


def _opt_str(v: object) -> str | None:
    if v is None or v == "":
        return None
    return str(v)


def _opt_int(v: object) -> int | None:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
