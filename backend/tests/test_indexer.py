"""Indexado incremental: la ruta más destructiva del proyecto, antes sin tests.

`ingest_vault` borra del índice todo documento que no vea en el vault. Un
`VAULT_DIR` mal montado vaciaba la colección entera y salía con código 0.
"""

from __future__ import annotations

import pytest

from rag.ingestion.indexer import (
    MAX_PURGE_RATIO,
    IngestStats,
    doc_id_for,
    ensure_collection,
    ingest_vault,
)

FRONTMATTER = """---
source_file: "{name}.pdf"
source_hash: "{h}"
pages: 2
tipo: "contrato"
ruc_usuario_libre: "20467534026"
tags:
  - osinergmin
---

# {name}

Cláusula primera. La potencia contratada es de 10 MW en horas punta.

## Página 2

Cláusula segunda. El precio de la energía es 45 USD/MWh.
"""


class FakeQdrant:
    """Doble de QdrantClient con solo lo que usa el indexer."""

    def __init__(self, points: dict[str, str] | None = None, exists: bool = True):
        # points: doc_id -> source_hash
        self.points = dict(points or {})
        self.exists = exists
        self.deleted: list[str] = []
        self.upserted: list[str] = []
        self.indexes: list[str] = []
        self.created = False

    def collection_exists(self, name):  # noqa: ARG002
        return self.exists

    def create_collection(self, collection_name, vectors_config):  # noqa: ARG002
        self.created = True
        self.exists = True

    def create_payload_index(self, collection_name, field_name, field_schema):  # noqa: ARG002
        self.indexes.append(field_name)

    def scroll(  # noqa: ARG002
        self, collection_name, with_payload=None, with_vectors=None, limit=None, offset=None
    ):
        items = [
            type("P", (), {"payload": {"doc_id": d, "source_hash": h}, "id": d})()
            for d, h in self.points.items()
        ]
        return items, None

    def delete(self, collection_name, points_selector, wait=None):  # noqa: ARG002
        cond = points_selector.filter.must[0]
        doc_id = cond.match.value
        self.deleted.append(doc_id)
        self.points.pop(doc_id, None)

    def upsert(self, collection_name, points, wait=None):  # noqa: ARG002
        for p in points:
            self.upserted.append(p.payload["doc_id"])
            self.points[p.payload["doc_id"]] = p.payload["source_hash"]


class FakeEmbedder:
    def __init__(self, dim: int = 4, fail_after: int | None = None):
        self.dim = dim
        self.calls = 0
        self.fail_after = fail_after

    def dimension(self) -> int:
        return self.dim

    def embed(self, texts):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("Ollama sin memoria")
        return [[0.1] * self.dim for _ in texts]


def write_doc(vault, name: str, h: str = "hash1") -> None:
    (vault / f"{name}.md").write_text(FRONTMATTER.format(name=name, h=h), encoding="utf-8")


def run(vault, client, embedder=None, **kw) -> IngestStats:
    return ingest_vault(
        vault_dir=vault,
        client=client,
        embedder=embedder or FakeEmbedder(),
        collection="c",
        manifest_path=vault / ".state" / "manifest.jsonl",
        **kw,
    )


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    return v


class TestEnsureCollection:
    def test_crea_la_coleccion_y_sus_indices(self):
        client = FakeQdrant(exists=False)
        ensure_collection(client, "c", 4)
        assert client.created
        assert "text" in client.indexes
        assert "ruc_usuario_libre" in client.indexes

    def test_reconcilia_indices_en_una_coleccion_preexistente(self):
        """Antes retornaba temprano si la colección existía, así que una
        colección creada por una versión anterior se quedaba sin índice
        full-text y el retrieval híbrido degradaba a solo-denso en silencio."""
        client = FakeQdrant(exists=True)
        ensure_collection(client, "c", 4)
        assert not client.created
        assert "text" in client.indexes

    def test_un_indice_ya_existente_no_revienta(self):
        client = FakeQdrant(exists=True)

        def boom(collection_name, field_name, field_schema):
            raise RuntimeError("index already exists")

        client.create_payload_index = boom
        ensure_collection(client, "c", 4)  # no lanza


class TestIngest:
    def test_indexa_un_documento_nuevo(self, vault):
        write_doc(vault, "a")
        client = FakeQdrant()
        stats = run(vault, client)
        assert stats.indexed == 1
        assert stats.chunks_upserted > 0
        assert stats.ok

    def test_salta_lo_que_no_cambio(self, vault):
        write_doc(vault, "a", h="hash1")
        client = FakeQdrant({"a": "hash1"})
        stats = run(vault, client)
        assert stats.skipped == 1
        assert stats.indexed == 0

    def test_reindexa_cuando_cambia_el_hash(self, vault):
        write_doc(vault, "a", h="hash2")
        client = FakeQdrant({"a": "hash1"})
        stats = run(vault, client)
        assert stats.indexed == 1
        assert "a" in client.deleted  # los chunks viejos se purgan

    def test_embebe_antes_de_borrar(self, vault):
        """Borrar y luego embeber no es atómico: si el embedder fallaba en
        medio, el documento desaparecía del índice en las dos versiones."""
        write_doc(vault, "a", h="hash2")
        client = FakeQdrant({"a": "hash1"})
        embedder = FakeEmbedder(fail_after=0)  # falla en la primera llamada
        stats = run(vault, client, embedder)
        assert stats.failed == 1
        assert client.deleted == []  # el documento viejo sigue consultable
        assert client.points["a"] == "hash1"

    def test_force_purga_los_chunks_viejos(self, vault):
        """Con --force, `existing` quedaba vacío, así que nunca se borraba: los
        ids son uuid5 del source_hash, de modo que quedaban dos generaciones
        del mismo contrato en el índice y el agente las reportaba como
        contradicción entre documentos."""
        write_doc(vault, "a", h="hash2")
        client = FakeQdrant({"a": "hash1"})
        run(vault, client, force=True)
        assert "a" in client.deleted

    def test_force_reindexa_aunque_el_hash_coincida(self, vault):
        write_doc(vault, "a", h="hash1")
        client = FakeQdrant({"a": "hash1"})
        stats = run(vault, client, force=True)
        assert stats.indexed == 1
        assert stats.skipped == 0


class TestPurgaDeStale:
    def test_purga_un_documento_borrado_del_vault(self, vault):
        write_doc(vault, "a")
        client = FakeQdrant({"a": "hash1", "viejo": "h"})
        stats = run(vault, client)
        assert stats.deleted_stale == 1
        assert client.deleted == ["viejo"]

    def test_un_vault_vacio_no_borra_el_indice(self, vault):
        """El caso que vaciaba la colección: compose crea ./data/vault vacío,
        así que `--vault` pasaba la validación, `iter_vault` devolvía [] y el
        bucle purgaba todo reportando éxito."""
        client = FakeQdrant({"a": "h", "b": "h", "c": "h"})
        stats = run(vault, client)
        assert client.deleted == []
        assert stats.deleted_stale == 0
        assert stats.purge_skipped == 3
        assert not stats.ok  # el CLI sale con 1

    def test_no_purga_mas_de_la_mitad_del_indice(self, vault):
        write_doc(vault, "a")
        client = FakeQdrant({f"doc{i}": "h" for i in range(10)} | {"a": "otro"})
        stats = run(vault, client)
        assert client.deleted == ["a"]  # solo el reindexado, ninguna purga
        assert stats.purge_skipped == 10
        assert not stats.ok

    def test_allow_purge_permite_el_borrado_masivo(self, vault):
        write_doc(vault, "a")
        client = FakeQdrant({f"doc{i}": "h" for i in range(10)} | {"a": "hash1"})
        stats = run(vault, client, max_purge_ratio=1.0)
        assert stats.deleted_stale == 10
        assert stats.ok

    def test_un_documento_ilegible_no_se_purga(self, vault):
        """Un EIO transitorio en un montaje de red hacía que load_document
        devolviera None; el doc quedaba fuera de `seen` y la purga lo borraba
        del índice, dejando el contrato inconsultable hasta la corrida
        siguiente."""
        write_doc(vault, "a")
        # Documento que load_document rechaza (cuerpo vacío tras el frontmatter).
        (vault / "a.md").write_text(
            '---\nsource_hash: "hash1"\ntipo: "contrato"\n---\n\n   \n', encoding="utf-8"
        )
        client = FakeQdrant({"a": "hash1"})
        stats = run(vault, client)
        assert stats.failed == 1
        assert client.deleted == []
        assert stats.deleted_stale == 0

    def test_el_umbral_por_defecto_es_conservador(self):
        assert 0 < MAX_PURGE_RATIO <= 0.5


class TestDocIdFor:
    def test_es_la_ruta_relativa_sin_extension(self, tmp_path):
        p = tmp_path / "contratos" / "a.md"
        p.parent.mkdir()
        p.touch()
        assert doc_id_for(p, tmp_path) == "contratos/a"

    def test_coincide_con_el_que_produce_el_loader(self, vault):
        from rag.ingestion.loader import load_document

        write_doc(vault, "a")
        doc = load_document(vault / "a.md", vault)
        assert doc is not None
        assert doc.doc_id == doc_id_for(vault / "a.md", vault)


class TestManifest:
    def test_escribe_una_linea_por_documento(self, vault):
        write_doc(vault, "a")
        run(vault, FakeQdrant())
        manifest = vault / ".state" / "manifest.jsonl"
        assert manifest.exists()
        assert len(manifest.read_text(encoding="utf-8").splitlines()) == 1

    def test_registra_los_fallos(self, vault):
        write_doc(vault, "a")
        run(vault, FakeQdrant(), FakeEmbedder(fail_after=0))
        manifest = vault / ".state" / "manifest.jsonl"
        assert "failed" in manifest.read_text(encoding="utf-8")
