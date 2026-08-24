"""Cuota diaria por IP: el contador SQLite y su contrato HTTP en /api/chat.

Lo que rompería el despliegue público si regresara:
- una IP que agotó su cuota y sigue chateando (el LLM local atiende en serie);
- la cuota reiniciándose con cada deploy (debe persistir en disco);
- la API key dejando de saltar la cuota (uso propio ilimitado);
- /api/documents abierto sin clave en modo público (publica RUCs).
"""

from __future__ import annotations

import datetime as dt

import pytest

from rag.api.quota import DailyQuota
from rag.config import Settings

from .test_api import FakeAgent, build, chain


class TestDailyQuota:
    def test_permite_hasta_el_limite_y_luego_niega(self, tmp_path):
        q = DailyQuota(str(tmp_path / "q.sqlite3"), daily_limit=3)
        assert [q.hit("1.2.3.4") for _ in range(3)] == [(True, 2), (True, 1), (True, 0)]
        assert q.hit("1.2.3.4") == (False, 0)

    def test_ips_distintas_no_comparten_cubo(self, tmp_path):
        q = DailyQuota(str(tmp_path / "q.sqlite3"), daily_limit=1)
        assert q.hit("1.1.1.1")[0] is True
        assert q.hit("2.2.2.2")[0] is True
        assert q.hit("1.1.1.1")[0] is False

    def test_persiste_entre_reinicios_del_proceso(self, tmp_path):
        path = str(tmp_path / "q.sqlite3")
        q1 = DailyQuota(path, daily_limit=2)
        q1.hit("9.9.9.9")
        q1.close()
        q2 = DailyQuota(path, daily_limit=2)
        assert q2.hit("9.9.9.9") == (True, 0)
        assert q2.hit("9.9.9.9") == (False, 0)

    def test_negar_no_consume_ni_deja_negativos(self, tmp_path):
        q = DailyQuota(str(tmp_path / "q.sqlite3"), daily_limit=1)
        q.hit("8.8.8.8")
        for _ in range(3):
            assert q.hit("8.8.8.8") == (False, 0)
        assert q.remaining("8.8.8.8") == 0

    def test_remaining_no_consume(self, tmp_path):
        q = DailyQuota(str(tmp_path / "q.sqlite3"), daily_limit=5)
        assert q.remaining("7.7.7.7") == 5
        q.hit("7.7.7.7")
        assert q.remaining("7.7.7.7") == 4
        assert q.remaining("7.7.7.7") == 4

    def test_purga_dias_viejos(self, tmp_path):
        q = DailyQuota(str(tmp_path / "q.sqlite3"), daily_limit=2)
        old = (dt.datetime.now(dt.UTC).date() - dt.timedelta(days=30)).isoformat()
        with q._lock:
            q._conn.execute("INSERT INTO quota VALUES (?, ?, ?)", ("3.3.3.3", old, 2))
            q._conn.commit()
        q.hit("4.4.4.4")  # dispara la purga del día
        with q._lock:
            rows = q._conn.execute("SELECT day FROM quota").fetchall()
        assert all(day != old for (day,) in rows)


@pytest.fixture
def public_settings(tmp_path):
    return Settings(
        api_key="clave-secreta",
        public_chat=True,
        chat_daily_limit=2,
        quota_db_path=str(tmp_path / "quota.sqlite3"),
        _env_file=None,
    )


def build_public(settings: Settings):
    client = build(settings, agent=FakeAgent([chain("generate", {"answer": "ok"}, start=False)]))
    client.app.state.quota = DailyQuota(settings.quota_db_path, settings.chat_daily_limit)
    return client


class TestPublicChat:
    def test_anonimo_puede_chatear_hasta_la_cuota(self, public_settings):
        client = build_public(public_settings)
        for expected_remaining in ("1", "0"):
            r = client.post(
                "/api/chat",
                json={"question": "¿qué contratos vencen?"},
                headers={"X-Real-IP": "5.5.5.5"},
            )
            assert r.status_code == 200
            assert r.headers["x-quota-remaining"] == expected_remaining
        r = client.post(
            "/api/chat",
            json={"question": "otra"},
            headers={"X-Real-IP": "5.5.5.5"},
        )
        assert r.status_code == 429
        assert "límite" in r.json()["detail"]
        assert r.headers["retry-after"] == "86400"

    def test_api_key_valida_salta_la_cuota(self, public_settings):
        client = build_public(public_settings)
        for _ in range(5):  # muy por encima de chat_daily_limit=2
            r = client.post(
                "/api/chat",
                json={"question": "x"},
                headers={"X-API-Key": "clave-secreta", "X-Real-IP": "6.6.6.6"},
            )
            assert r.status_code == 200
            assert "x-quota-remaining" not in r.headers

    def test_documents_sigue_exigiendo_clave_en_modo_publico(self, public_settings):
        client = build_public(public_settings)
        assert client.get("/api/documents").status_code == 401
        r = client.get("/api/documents", headers={"X-API-Key": "clave-secreta"})
        assert r.status_code == 200

    def test_ips_distintas_tienen_cuotas_separadas(self, public_settings):
        client = build_public(public_settings)
        for ip in ("10.0.0.1", "10.0.0.2"):
            r = client.post(
                "/api/chat", json={"question": "x"}, headers={"X-Real-IP": ip}
            )
            assert r.status_code == 200

    def test_sin_public_chat_el_anonimo_sigue_en_401(self, tmp_path):
        settings = Settings(api_key="clave-secreta", public_chat=False, _env_file=None)
        client = build(
            settings, agent=FakeAgent([chain("generate", {"answer": "ok"}, start=False)])
        )
        client.app.state.quota = None
        r = client.post("/api/chat", json={"question": "x"})
        assert r.status_code == 401
