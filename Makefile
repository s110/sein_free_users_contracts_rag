.PHONY: help setup models lint test typecheck up down ingest ingest-force logs eval dev-backend dev-frontend

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Instala deps de backend (uv) y frontend (npm)
	cd backend && uv sync --all-extras
	cd frontend && npm install

models: ## Descarga los modelos locales en Ollama
	ollama pull qwen3:4b
	ollama pull bge-m3

lint: ## Ruff (backend) + typecheck (frontend)
	cd backend && uv run ruff check src tests && uv run ruff format --check src tests
	cd frontend && npm run typecheck

test: ## Tests del backend (no requiere servicios)
	cd backend && uv run pytest -q

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
	cd backend && uv run python eval/run_eval.py

dev-backend: ## Backend en modo dev (nativo, hot reload)
	cd backend && RAG_QDRANT_URL=http://localhost:6333 RAG_OLLAMA_HOST=http://localhost:11434 \
		uv run uvicorn rag.api.main:app --reload --port 8000

dev-frontend: ## Frontend en modo dev (Vite, proxy a :8000)
	cd frontend && npm run dev
