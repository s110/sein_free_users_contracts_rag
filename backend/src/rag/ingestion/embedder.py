"""Cliente de embeddings contra Ollama (/api/embed) con batching y retries.

Se usa httpx directo (no el SDK) para controlar timeouts, backoff y tamaño
de batch — en un Mac Mini de 16GB el embedder comparte memoria con el LLM,
así que los batches se mantienen pequeños y los errores transitorios
(modelo cargándose, OOM momentáneo) se reintentan con backoff exponencial.
"""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("rag.embedder")


class OllamaEmbedder:
    def __init__(
        self,
        host: str,
        model: str,
        batch_size: int = 16,
        timeout: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.batch_size = batch_size
        self.max_retries = max_retries
        self._client = httpx.Client(timeout=timeout)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            out.extend(self._embed_batch(batch))
        return out

    def embed_one(self, text: str) -> list[float]:
        return self._embed_batch([text])[0]

    def dimension(self) -> int:
        """Detecta la dimensión del modelo con una llamada mínima."""
        return len(self.embed_one("dim probe"))

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.post(
                    f"{self.host}/api/embed",
                    json={"model": self.model, "input": batch},
                )
                resp.raise_for_status()
                data = resp.json()
                embeddings = data.get("embeddings")
                if not embeddings or len(embeddings) != len(batch):
                    raise ValueError(
                        f"Ollama devolvió {len(embeddings or [])} embeddings "
                        f"para {len(batch)} textos"
                    )
                return embeddings
            except (httpx.HTTPError, ValueError) as e:
                last_error = e
                wait = 2.0**attempt
                log.warning(
                    "Embed batch falló (intento %d/%d): %s — retry en %.0fs",
                    attempt + 1,
                    self.max_retries + 1,
                    e,
                    wait,
                )
                time.sleep(wait)
        raise RuntimeError(
            f"Embeddings fallaron tras {self.max_retries + 1} intentos"
        ) from last_error

    def close(self) -> None:
        self._client.close()
