from pathlib import Path

from rag.ingestion.loader import iter_vault, load_document, split_frontmatter

SAMPLE_MD = """---
source_file: "ATRE_20467534026_20250514_8789_00.pdf"
source_hash: a3f9b2c1d4e5f6a7
created: 2026-07-02T14:32:15-05:00
pages: 12
suministrador_code: "ATRE"
ruc_usuario_libre: "20467534026"
fecha_suscripcion: "2025-05-14"
tipo: "contrato"
tags:
  - osinergmin
  - contrato
---

## Página 1

CONTRATO DE SUMINISTRO DE ELECTRICIDAD

La potencia contratada es de 5 MW.
"""


def test_split_frontmatter_parses_yaml_and_body():
    fm, body = split_frontmatter(SAMPLE_MD)
    assert fm["source_hash"] == "a3f9b2c1d4e5f6a7"
    assert fm["tipo"] == "contrato"
    assert body.startswith("## Página 1")


def test_split_frontmatter_without_frontmatter():
    fm, body = split_frontmatter("solo texto plano")
    assert fm == {}
    assert body == "solo texto plano"


def test_split_frontmatter_invalid_yaml_falls_back():
    text = "---\n: : broken [yaml\n---\ncuerpo"
    fm, body = split_frontmatter(text)
    assert fm == {}
    assert body == text


def test_load_document_maps_metadata(tmp_path: Path):
    md = tmp_path / "contratos" / "doc.md"
    md.parent.mkdir()
    md.write_text(SAMPLE_MD, encoding="utf-8")
    doc = load_document(md, tmp_path)
    assert doc is not None
    assert doc.doc_id == "contratos/doc"
    assert doc.meta.ruc_usuario_libre == "20467534026"
    assert doc.meta.source_hash == "a3f9b2c1d4e5f6a7"
    assert "osinergmin" in doc.meta.tags


def test_load_document_without_hash_uses_content_hash(tmp_path: Path):
    md = tmp_path / "sin_hash.md"
    md.write_text("---\nsource_file: x.pdf\n---\ncontenido", encoding="utf-8")
    doc = load_document(md, tmp_path)
    assert doc is not None
    assert len(doc.meta.source_hash) == 16


def test_load_document_empty_body_returns_none(tmp_path: Path):
    md = tmp_path / "vacio.md"
    md.write_text("---\nsource_hash: abc12345\n---\n\n", encoding="utf-8")
    assert load_document(md, tmp_path) is None


def test_iter_vault_skips_system_dirs(tmp_path: Path):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    ocr_dir = tmp_path / ".ocr"
    ocr_dir.mkdir()
    (ocr_dir / "manifest.md").write_text("x", encoding="utf-8")
    files = iter_vault(tmp_path)
    assert [f.name for f in files] == ["a.md"]
