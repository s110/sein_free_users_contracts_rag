"""Cuota diaria de chat por IP, persistida en SQLite.

El recurso que protege no es ancho de banda sino el LLM local: un modelo de
4B en un Mac mini atiende en serie, y sin cuota una sola IP podía monopolizar
el servicio para todo internet. nginx ya limita ráfagas por minuto; esto
limita el *total del día*, que es lo que de verdad acota el costo.

SQLite y no memoria: el contador debe sobrevivir a un `docker compose up -d
--build` (deploy rutinario), o cada release regalaría cuota fresca a quien
estuviera abusando. WAL para que el healthcheck y dos requests concurrentes
no se bloqueen entre sí. El día es UTC a propósito: es verificable desde
fuera y no depende de la zona horaria del contenedor.
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger("rag.quota")

_PRUNE_KEEP_DAYS = 7


class DailyQuota:
    """`hit(ip)` incrementa y responde si la IP aún tiene cuota hoy.

    Una única conexión protegida por lock: el tráfico con cuota es por
    definición minúsculo (N preguntas/día por IP) y una conexión evita los
    problemas de SQLite con conexiones por-hilo en el threadpool de asyncio.
    """

    def __init__(self, db_path: str, daily_limit: int) -> None:
        self.daily_limit = daily_limit
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: se usa desde asyncio.to_thread, siempre
        # bajo self._lock, nunca desde dos hilos a la vez.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._pruned_day: str | None = None
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS quota ("
                " ip TEXT NOT NULL,"
                " day TEXT NOT NULL,"
                " count INTEGER NOT NULL,"
                " PRIMARY KEY (ip, day))"
            )
            self._conn.commit()

    @staticmethod
    def _today() -> str:
        return dt.datetime.now(dt.UTC).date().isoformat()

    def hit(self, ip: str) -> tuple[bool, int]:
        """Consume un intento de `ip`. Devuelve (permitido, restantes_hoy).

        El incremento es un único UPSERT: dos requests simultáneos de la
        misma IP no pueden colarse ambos por una ventana de leer-luego-escribir.
        Un intento denegado no incrementa: reintentar mañana no está penado.
        """
        day = self._today()
        with self._lock:
            self._prune_if_new_day(day)
            row = self._conn.execute(
                "INSERT INTO quota (ip, day, count) VALUES (?, ?, 1)"
                " ON CONFLICT (ip, day) DO UPDATE SET count = count + 1"
                " RETURNING count",
                (ip, day),
            ).fetchone()
            count = int(row[0])
            if count > self.daily_limit:
                # Devolver el contador al límite: los rechazos no inflan el
                # número y `remaining` se mantiene en 0, no en negativo.
                self._conn.execute(
                    "UPDATE quota SET count = ? WHERE ip = ? AND day = ?",
                    (self.daily_limit, ip, day),
                )
                self._conn.commit()
                return False, 0
            self._conn.commit()
            return True, self.daily_limit - count

    def remaining(self, ip: str) -> int:
        """Cuota restante de `ip` hoy, sin consumir."""
        with self._lock:
            row = self._conn.execute(
                "SELECT count FROM quota WHERE ip = ? AND day = ?",
                (ip, self._today()),
            ).fetchone()
        used = int(row[0]) if row else 0
        return max(0, self.daily_limit - used)

    def _prune_if_new_day(self, day: str) -> None:
        """Borra días viejos una vez por día de proceso; llamar con el lock."""
        if self._pruned_day == day:
            return
        cutoff = (dt.date.fromisoformat(day) - dt.timedelta(days=_PRUNE_KEEP_DAYS)).isoformat()
        deleted = self._conn.execute("DELETE FROM quota WHERE day < ?", (cutoff,)).rowcount
        self._pruned_day = day
        if deleted:
            log.info("Cuota: purgadas %d filas anteriores a %s", deleted, cutoff)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
