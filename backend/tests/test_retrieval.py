from rag.retrieval.store import _build_filter, rrf_fuse


def test_rrf_prefers_items_in_both_rankings():
    dense = ["a", "b", "c"]
    lexical = ["c", "d"]
    scores = rrf_fuse([dense, lexical])
    assert scores["c"] > scores["a"]  # 'c' aparece en ambas listas
    assert scores["a"] > scores["b"]


def test_rrf_empty_rankings():
    assert rrf_fuse([[], []]) == {}


def test_build_filter_only_allows_known_fields():
    f = _build_filter({"ruc_usuario_libre": "20467534026", "malicioso": "x", "tipo": ""})
    assert f is not None
    keys = [c.key for c in f.must]
    assert keys == ["ruc_usuario_libre"]


def test_build_filter_none_when_empty():
    assert _build_filter(None) is None
    assert _build_filter({}) is None
    assert _build_filter({"desconocido": "x"}) is None
