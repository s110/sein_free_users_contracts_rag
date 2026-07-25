"""CLI de ingesta: el código de salida es lo que ve el cron.

`return 1 if failed and not indexed and not skipped else 0` daba éxito a una
corrida con 1 indexado y 500 fallidos.
"""

from __future__ import annotations

import pytest

import rag.ingestion.cli as cli
from rag.ingestion.indexer import IngestStats


@pytest.fixture
def entorno(monkeypatch, tmp_path):
    """Aísla el CLI de Qdrant, Ollama y la config del entorno."""
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("RAG_VAULT_DIR", str(vault))
    monkeypatch.setenv("RAG_MANIFEST_PATH", str(tmp_path / "manifest.jsonl"))
    monkeypatch.setenv("RAG_API_KEY", "k")
    monkeypatch.setattr(cli, "QdrantClient", lambda url, timeout: object())
    monkeypatch.setattr(
        cli,
        "OllamaEmbedder",
        lambda **kw: type("E", (), {"close": lambda self: None})(),
    )
    # argparse lee sys.argv: sin esto el CLI ve los flags de pytest.
    monkeypatch.setattr("sys.argv", ["sein-rag-ingest"])
    return vault


def with_stats(monkeypatch, stats: IngestStats, capture: dict | None = None):
    def fake(**kw):
        if capture is not None:
            capture.update(kw)
        return stats

    monkeypatch.setattr(cli, "ingest_vault", fake)


class TestExitCode:
    def test_corrida_limpia_da_0(self, entorno, monkeypatch):
        with_stats(monkeypatch, IngestStats(scanned=3, indexed=3))
        assert cli.main() == 0

    def test_un_solo_fallo_ya_da_1(self, entorno, monkeypatch):
        """Antes: 1 indexado + 500 fallidos salía con 0 y el cron lo daba por
        bueno mientras faltaban 500 contratos en el índice."""
        with_stats(monkeypatch, IngestStats(scanned=501, indexed=1, failed=500))
        assert cli.main() == 1

    def test_una_purga_abortada_da_1(self, entorno, monkeypatch):
        with_stats(monkeypatch, IngestStats(scanned=0, purge_skipped=12))
        assert cli.main() == 1

    def test_corrida_sin_cambios_da_0(self, entorno, monkeypatch):
        with_stats(monkeypatch, IngestStats(scanned=5, skipped=5))
        assert cli.main() == 0

    def test_vault_inexistente_da_2(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RAG_VAULT_DIR", str(tmp_path / "no-existe"))
        monkeypatch.setattr("sys.argv", ["sein-rag-ingest"])
        assert cli.main() == 2


class TestFlags:
    def test_force_se_propaga(self, entorno, monkeypatch):
        capture: dict = {}
        with_stats(monkeypatch, IngestStats(), capture)
        monkeypatch.setattr("sys.argv", ["sein-rag-ingest", "--force"])
        cli.main()
        assert capture["force"] is True

    def test_sin_allow_purge_se_usa_el_umbral_conservador(self, entorno, monkeypatch):
        from rag.ingestion.indexer import MAX_PURGE_RATIO

        capture: dict = {}
        with_stats(monkeypatch, IngestStats(), capture)
        monkeypatch.setattr("sys.argv", ["sein-rag-ingest"])
        cli.main()
        assert capture["max_purge_ratio"] == MAX_PURGE_RATIO

    def test_allow_purge_levanta_el_umbral(self, entorno, monkeypatch):
        capture: dict = {}
        with_stats(monkeypatch, IngestStats(), capture)
        monkeypatch.setattr("sys.argv", ["sein-rag-ingest", "--allow-purge"])
        cli.main()
        assert capture["max_purge_ratio"] == 1.0

    def test_vault_por_flag_gana(self, entorno, monkeypatch, tmp_path):
        otro = tmp_path / "otro"
        otro.mkdir()
        capture: dict = {}
        with_stats(monkeypatch, IngestStats(), capture)
        monkeypatch.setattr("sys.argv", ["sein-rag-ingest", "--vault", str(otro)])
        cli.main()
        assert capture["vault_dir"] == otro


def test_el_embedder_se_cierra_aunque_falle_la_ingesta(entorno, monkeypatch):
    cerrado = {"si": False}

    class Embedder:
        def close(self):
            cerrado["si"] = True

    monkeypatch.setattr(cli, "OllamaEmbedder", lambda **kw: Embedder())

    def boom(**kw):
        raise RuntimeError("Qdrant caído")

    monkeypatch.setattr(cli, "ingest_vault", boom)
    monkeypatch.setattr("sys.argv", ["sein-rag-ingest"])
    with pytest.raises(RuntimeError):
        cli.main()
    assert cerrado["si"]
