# SEIN Free Users Contracts RAG

RAG agéntico local sobre los contratos de usuarios libres del SEIN. Arquitectura,
stack y comandos en `README.md`; despliegue en `docs/DEPLOYMENT.md`.

## Testing

- NEVER write unit tests after you write code.
- Highly prefer E2E tests as the sole testing mechanism. Use them to verify complex features work. At the end of E2E tests, produce a verifiable and repeatable artifact.
- If you must test a system in isolation, FIRST write all the ways it could fail, THEN write the code.
- When writing E2E tests don't pick the simplest possible scenario to prove it works; pick a medium to hard scenario when verifying the work with E2E tests.
- Tautological tests considered harmful.
- Change-detector tests considered harmful.
- Do not create regression tests for bug fixes without a genuine gap in behavior testing.

### E2E de esta repo

```bash
make e2e        # = cd backend && uv run python e2e/run_e2e.py   (~4 min)
```

Requiere Docker y Ollama nativo con `qwen3.5:4b` y `qwen3-embedding:0.6b`
(`make models`). No toca el stack de producción: levanta su propio Qdrant
efímero (`docker compose -p e2e-sein_free_users_contracts_rag`, puerto 16333) y
sirve la API en 18765; ambos se cambian con `E2E_QDRANT_PORT` / `E2E_API_PORT`.
Todo se desmonta al terminar, pase o falle. Nunca llama a DeepSeek.

Escenario (`backend/e2e/run_e2e.py`, fixtures en `backend/e2e/fixtures/`):
el CLI `sein-rag-ingest` real sobre un vault con frontmatter roto, tablas HTML
sin cerrar y `.ocr/`; reingesta idempotente; `--force` sin dos generaciones;
vault mutado (cambio de hash, borrado, documento vacío → exit 1); luego
uvicorn + `/api/chat` SSE con la trampa Celepsa→Pluz, un seguimiento con
historial, una adenda con filtros (pregunta corta que no nombra el contrato),
la cifra nueva del contrato reindexado, el verificador fundamentando esas
cuatro respuestas y rechazos fijos (fuera de tema, inyección, volcados
masivos); luego una segunda API cuyo Ollama pasa por un proxy que falsea una
cifra de la respuesta generada, y el verificador tiene que refutarla (puertos
`E2E_API_PORT`+1 y +2); al final los frenos de purga (vault vacío con
`--allow-purge`, purga masiva sin y con el flag).

Artefactos en `artifacts/e2e/` (ignorado por git):

- `report.json`: hashes de fixtures, modelos, observaciones deterministas
  (códigos de salida, estadísticas, digest del índice) y pass/fail por check.
  Sin tiempos ni texto del modelo: dos corridas en la misma máquina con los
  mismos modelos dan el mismo archivo. Verificar con
  `cd artifacts/e2e && shasum -a 256 -c report.sha256` y
  `jq .summary artifacts/e2e/report.json` (debe decir `"result": "PASS"`).
- `transcript.json`: respuestas completas, fuentes y el informe del
  verificador. Para leer, no para comparar.
- `api.log`: log JSON del backend durante la corrida.

`make test` (pytest + vitest, sin servicios) sigue corriendo en CI; el E2E no,
porque necesita Ollama.
