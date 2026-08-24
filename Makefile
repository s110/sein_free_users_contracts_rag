.PHONY: help setup models lint test cov audit ci typecheck up down ingest ingest-force \
        logs eval dev-backend dev-frontend

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Instala deps de backend (uv) y frontend (npm)
	cd backend && uv sync --locked --group dev
	cd frontend && npm ci

models: ## Descarga los modelos locales en Ollama
	ollama pull qwen3.5:4b
	ollama pull qwen3-embedding:0.6b

lint: ## Ruff (backend) + ESLint y typecheck (frontend)
	cd backend && uv run ruff check src tests eval && uv run ruff format --check src tests eval
	cd frontend && npm run lint && npm run typecheck

test: ## Tests de backend y frontend (no requieren servicios)
	cd backend && uv run pytest
	cd frontend && npm run test

cov: ## Cobertura de ambos lados
	cd backend && uv run pytest --cov-report=html
	cd frontend && npm run coverage

audit: ## Auditoría de vulnerabilidades (python + npm)
	cd backend && uv export --locked --no-dev --no-emit-project \
		--format requirements-txt -o requirements.txt \
		&& uvx pip-audit --strict --requirement requirements.txt \
		&& rm -f requirements.txt
	cd frontend && npm audit --audit-level=high

ci: lint test audit ## Todo lo que corre CI, en local

up: ## Levanta el stack (qdrant + backend + frontend)
	docker compose up -d --build

down: ## Baja el stack
	docker compose down

ingest: ## Ingesta incremental del vault (idempotente)
	docker compose --profile ingest run --rm ingest

ingest-force: ## Reindexa todo ignorando hashes
	docker compose --profile ingest run --rm ingest sein-rag-ingest --force

logs: ## Logs del backend
	docker compose logs -f backend

eval: ## Evalúa contra el golden set (requiere stack vivo)
	cd backend && uv run python eval/run_eval.py --min-hit-rate 0.7

dev-backend: ## Backend en modo dev (nativo, hot reload)
	cd backend && RAG_QDRANT_URL=http://localhost:6333 RAG_OLLAMA_HOST=http://localhost:11434 \
		uv run uvicorn rag.api.main:app --reload --port 8000

dev-frontend: ## Frontend en modo dev (Vite, proxy a :8000)
	cd frontend && npm run dev
