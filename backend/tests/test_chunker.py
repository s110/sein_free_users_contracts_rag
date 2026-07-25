import pytest

from rag.ingestion.chunker import chunk_document, chunk_id_for
from rag.schemas import DocumentMeta

META = DocumentMeta(source_file="doc.pdf", source_hash="abc123def4567890")


def test_chunk_ids_are_deterministic():
    a = chunk_id_for("d1", "hash1", 0)
    b = chunk_id_for("d1", "hash1", 0)
    c = chunk_id_for("d1", "hash2", 0)
    assert a == b
    assert a != c


def test_page_headers_tracked_not_kept_as_text():
    body = (
        "## Página 1\n\nCláusula primera: objeto del contrato.\n\n"
        "## Página 2\n\nCláusula segunda: potencia contratada 5 MW.\n"
    )
    chunks = chunk_document("d", body, META, max_chars=4000)
    assert len(chunks) == 1
    assert "Página" not in chunks[0].text
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 2


def test_real_headers_set_section_and_split():
    body = (
        "# CONTRATO DE SUMINISTRO\n\nTexto inicial.\n\n"
        "# ANEXO A: PRECIOS\n\nPrecio monomico 250 soles/MWh.\n"
    )
    chunks = chunk_document("d", body, META, max_chars=4000)
    assert len(chunks) == 2
    assert chunks[0].section == "CONTRATO DE SUMINISTRO"
    assert chunks[1].section == "ANEXO A: PRECIOS"
    assert "250" in chunks[1].text


def test_long_section_splits_with_overlap():
    paragraphs = [f"Párrafo {i}: " + "contenido contractual relevante. " * 8 for i in range(20)]
    body = "\n\n".join(paragraphs)
    chunks = chunk_document("d", body, META, max_chars=1000, overlap_chars=300)
    assert len(chunks) > 1
    assert all(len(c.text) <= 1000 + 300 for c in chunks)
    # overlap: algún contenido del final de un chunk reaparece al inicio del siguiente
    assert any(
        chunks[i].text.split("\n\n")[-1] in chunks[i + 1].text for i in range(len(chunks) - 1)
    )


def test_giant_block_hard_split():
    body = "A" * 10_000
    chunks = chunk_document("d", body, META, max_chars=2000, overlap_chars=200)
    assert len(chunks) >= 5
    assert all(len(c.text) <= 2000 for c in chunks)


def test_chunk_indices_are_sequential():
    body = "\n\n".join(f"Sección {i}. " + "x" * 500 for i in range(10))
    chunks = chunk_document("d", body, META, max_chars=800, overlap_chars=100)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_empty_body_returns_no_chunks():
    assert chunk_document("d", "\n\n  \n", META) == []


def test_overlap_igual_al_tamano_se_rechaza():
    """`overlap >= max_chars` hacía `step = 1` en `_hard_split`: un contrato
    de 200KB con una tabla grande producía 200.000 chunks y 12.500 llamadas a
    /api/embed por un solo documento."""
    from rag.ingestion.chunker import ChunkingError, chunk_document
    from rag.schemas import DocumentMeta

    meta = DocumentMeta(source_file="a.pdf", source_hash="h")
    with pytest.raises(ChunkingError, match="menor"):
        chunk_document("d", "texto", meta, max_chars=400, overlap_chars=400)


def test_overlap_mayor_que_el_tamano_se_rechaza():
    from rag.ingestion.chunker import ChunkingError, chunk_document
    from rag.schemas import DocumentMeta

    meta = DocumentMeta(source_file="a.pdf", source_hash="h")
    with pytest.raises(ChunkingError):
        chunk_document("d", "texto", meta, max_chars=100, overlap_chars=500)


def test_overlap_negativo_se_rechaza():
    from rag.ingestion.chunker import ChunkingError, chunk_document
    from rag.schemas import DocumentMeta

    meta = DocumentMeta(source_file="a.pdf", source_hash="h")
    with pytest.raises(ChunkingError):
        chunk_document("d", "texto", meta, max_chars=100, overlap_chars=-1)


def test_una_tabla_gigante_produce_un_numero_razonable_de_chunks():
    from rag.ingestion.chunker import chunk_document
    from rag.schemas import DocumentMeta

    meta = DocumentMeta(source_file="a.pdf", source_hash="h")
    chunks = chunk_document("d", "T" * 200_000, meta, max_chars=3200, overlap_chars=400)
    assert len(chunks) < 100
