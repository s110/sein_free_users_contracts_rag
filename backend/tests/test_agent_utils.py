from rag.graph.agent import format_context, format_history, parse_json_reply
from rag.schemas import RetrievedChunk


def _chunk(**kw) -> RetrievedChunk:
    base = {
        "chunk_id": "c1",
        "doc_id": "d1",
        "text": "La potencia contratada es 5 MW.",
        "score": 0.9,
        "source_file": "ATRE_20467534026_20250514_8789_00.pdf",
    }
    base.update(kw)
    return RetrievedChunk(**base)


def test_parse_json_reply_plain():
    assert parse_json_reply('{"relevant": true}') == {"relevant": True}


def test_parse_json_reply_with_fences():
    assert parse_json_reply('```json\n{"relevant": false}\n```') == {"relevant": False}


def test_parse_json_reply_with_prose_around():
    text = 'Claro, aquí está: {"search_query": "potencia contratada"} espero ayude'
    assert parse_json_reply(text)["search_query"] == "potencia contratada"


def test_parse_json_reply_garbage_returns_empty():
    assert parse_json_reply("no json aquí") == {}
    assert parse_json_reply("[1, 2, 3]") == {}


def test_format_context_numbers_and_pages():
    ctx = format_context([_chunk(page_start=3, page_end=4), _chunk(chunk_id="c2")])
    assert ctx.startswith("[1] ATRE_20467534026_20250514_8789_00.pdf, pág. 3-4")
    assert "[2]" in ctx


def test_format_history_truncates():
    history = [{"role": "user", "content": "x" * 1000}] * 20
    out = format_history(history)
    assert out.count("user:") == 8  # MAX_HISTORY_MESSAGES


def test_format_history_truncates_content_too():
    """El test anterior solo comprobaba el número de mensajes: borrar el
    `[:500]` del contenido lo dejaba pasando igual, y un historial largo se
    comía la ventana de contexto del modelo."""
    history = [{"role": "user", "content": "x" * 1000}]
    out = format_history(history)
    assert "x" * 501 not in out
    assert "x" * 500 in out


def test_format_history_empty():
    assert format_history([]) == "(sin historial)"


def test_format_history_tolerates_missing_keys():
    assert "?" in format_history([{}])


def test_format_context_includes_section():
    ctx = format_context([_chunk(section="Cláusula Tercera")])
    assert "Cláusula Tercera" in ctx


def test_format_context_single_page_has_no_range():
    ctx = format_context([_chunk(page_start=3, page_end=3)])
    assert "pág. 3" in ctx
    assert "3-3" not in ctx


def test_format_context_empty():
    assert format_context([]) == ""
