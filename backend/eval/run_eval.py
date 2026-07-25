"""Evaluación del RAG contra un golden set (eval/golden.jsonl).

Métricas:
- retrieval hit-rate: ¿el documento esperado aparece entre las fuentes?
- groundedness: veredicto del verificador del propio grafo
- respuestas completas guardadas en eval/results/<ts>.jsonl para revisión manual

Corre contra el stack vivo (Qdrant + Ollama), NO en CI:
    uv run python eval/run_eval.py [--golden eval/golden.jsonl]

Buenas prácticas: corre esto después de cambiar prompts (prompts.PROMPT_VERSION),
modelo o chunking, y compara el hit-rate contra la corrida anterior antes de
desplegar el cambio.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qdrant_client import QdrantClient  # noqa: E402

from rag.config import get_settings  # noqa: E402
from rag.graph import prompts  # noqa: E402
from rag.graph.agent import ContractsAgent  # noqa: E402
from rag.ingestion.embedder import OllamaEmbedder  # noqa: E402
from rag.retrieval.store import HybridStore  # noqa: E402


async def run(golden_path: Path, min_hit_rate: float | None, min_grounded: float | None) -> int:
    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url, timeout=60)
    embedder = OllamaEmbedder(host=settings.ollama_host, model=settings.embedding_model)
    store = HybridStore(client, embedder, settings.collection)
    agent = ContractsAgent(settings, store)

    cases = [json.loads(line) for line in golden_path.read_text().splitlines() if line.strip()]
    results = []
    hits = grounded_count = 0

    for i, case in enumerate(cases, 1):
        question = case["question"]
        print(f"[{i}/{len(cases)}] {question[:80]}")
        state = await agent.graph.ainvoke({"question": question, "history": [], "rewrites": 0})
        sources = [d.source_file for d in state.get("relevant_documents", [])]
        expected = case.get("expected_source_file", "")
        hit = bool(expected) and expected in sources
        grounded = state.get("grounded")
        hits += int(hit)
        grounded_count += int(bool(grounded))
        results.append(
            {
                "question": question,
                "answer": state.get("answer", ""),
                "sources": sources,
                "expected_source_file": expected,
                "retrieval_hit": hit,
                "grounded": grounded,
                "rewrites": state.get("rewrites", 0),
                "no_context": bool(state.get("no_context")),
            }
        )

    with_expected = sum(1 for c in cases if c.get("expected_source_file"))
    out_dir = golden_path.parent / "results"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_file = out_dir / f"{ts}.jsonl"
    with out_file.open("w") as f:
        header = {
            "prompt_version": prompts.PROMPT_VERSION,
            "llm": settings.llm_model,
            "embeddings": settings.embedding_model,
            "cases": len(cases),
            "retrieval_hit_rate": round(hits / with_expected, 3) if with_expected else None,
            "grounded_rate": round(grounded_count / len(cases), 3) if cases else None,
        }
        f.write(json.dumps(header, ensure_ascii=False) + "\n")
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n== Resultados ({out_file}) ==")
    print(json.dumps(header, indent=2, ensure_ascii=False))
    embedder.close()
    client.close()  # antes solo se cerraba el embedder

    code = 0
    hit_rate = header["retrieval_hit_rate"]
    grounded_rate = header["grounded_rate"]
    if min_hit_rate is not None:
        if hit_rate is None:
            print("FALLO: ningún caso del golden set tiene expected_source_file")
            code = 1
        elif hit_rate < min_hit_rate:
            print(f"FALLO: hit-rate {hit_rate} < umbral {min_hit_rate}")
            code = 1
    if min_grounded is not None and (grounded_rate is None or grounded_rate < min_grounded):
        print(f"FALLO: groundedness {grounded_rate} < umbral {min_grounded}")
        code = 1
    return code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=Path(__file__).parent / "golden.jsonl")
    parser.add_argument(
        "--min-hit-rate",
        type=float,
        default=None,
        help="Sale con 1 si el hit-rate de retrieval queda por debajo",
    )
    parser.add_argument(
        "--min-grounded",
        type=float,
        default=None,
        help="Sale con 1 si la tasa de respuestas verificadas queda por debajo",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.golden, args.min_hit_rate, args.min_grounded))


if __name__ == "__main__":
    sys.exit(main())
