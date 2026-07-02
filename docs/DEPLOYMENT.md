# Despliegue en el Mac Mini para usuarios externos

Objetivo: que personas fuera de tu red usen la app vía HTTPS, con el Mac
Mini M4 (16GB) como único servidor, sin abrir puertos en el router y sin
costos de nube.

## Arquitectura de exposición

```
usuario externo ──HTTPS──▶ Cloudflare Edge ──túnel saliente──▶ cloudflared (Docker)
                                                                    │
                                                              nginx (frontend)
                                                              ├─ estáticos React
                                                              └─ /api → backend → Qdrant
                                                                          └─▶ Ollama (host, Metal)
```

Cloudflare Tunnel abre una conexión **saliente** desde el Mac mini hacia
Cloudflare; nunca se abre un puerto entrante. TLS, DDoS protection y (si
quieres) login con Google/GitHub vienen incluidos en el plan gratuito.

## Paso 0 — Requisitos en el Mac Mini

```bash
brew install ollama
brew services start ollama          # arranca al login
ollama pull qwen3:4b && ollama pull bge-m3

# Docker Desktop (o OrbStack, más liviano) con "Start at login" activado
```

> **Por qué Ollama va nativo y no en Docker**: Docker en macOS no accede a
> la GPU Metal; en contenedor los modelos correrían en CPU (5-10x más
> lento). Misma convención que el repo `ocr_pdf_markdown`.

Para que Ollama siga vivo sin sesión iniciada, en Ajustes del Sistema →
Usuarios: activa inicio de sesión automático, y en Economizador desactiva
la suspensión (`sudo pmset -a sleep 0 displaysleep 10`).

## Paso 1 — Seguridad primero

```bash
cp .env.example .env
# Genera y define la API key (obligatoria si expones a internet):
echo "RAG_API_KEY=$(openssl rand -hex 24)" >> .env
```

- La API key protege `POST /api/chat` y `GET /api/documents`. Los usuarios
  la ingresan una vez en el engranaje ⚙ de la UI (queda en su navegador).
- nginx aplica rate limit: 10 consultas/min por IP al chat (cada consulta
  ocupa el LLM decenas de segundos; esto protege al Mac mini de abuso).
- Qdrant y el backend solo escuchan en `127.0.0.1` del host; únicamente
  nginx (frontend) es alcanzable desde el túnel.

## Paso 2 — Cloudflare Tunnel

1. Dominio en Cloudflare (uno propio o un subdominio gratuito).
2. [Zero Trust dashboard](https://one.dash.cloudflare.com) → Networks →
   Tunnels → **Create tunnel** (tipo *Cloudflared*).
3. Copia el token del túnel a `.env`:
   ```
   CLOUDFLARE_TUNNEL_TOKEN=eyJh...
   ```
4. En "Public Hostnames" del túnel agrega:
   - Hostname: `contratos.tudominio.com`
   - Service: `http://frontend:80` (cloudflared corre en la misma red de
     compose, resuelve el servicio por nombre).
5. Levanta todo:
   ```bash
   docker compose --profile public up -d --build
   ```
6. Verifica: `https://contratos.tudominio.com` desde datos móviles.

### Opcional: login sin gestionar usuarios (recomendado)

En Zero Trust → Access → Applications crea una app para
`contratos.tudominio.com` con política "Allow" por lista de emails. Los
usuarios se autentican con un código a su correo antes de llegar a la app
— cero código extra y puedes revocar acceso al instante.

### Alternativa sin Cloudflare: Tailscale Funnel

```bash
brew install tailscale
tailscale up
tailscale funnel 8080        # publica https://<mac-mini>.<tailnet>.ts.net
```

Menos configuración, pero URL menos amigable y sin WAF/analytics.

## Paso 3 — Supervivencia a reinicios

Todos los servicios llevan `restart: unless-stopped`; la cadena completa es:

| Componente | Mecanismo de arranque |
|---|---|
| Ollama | `brew services` (login item) |
| Docker Desktop/OrbStack | "Start at login" |
| qdrant/backend/frontend/cloudflared | `restart: unless-stopped` |
| Ingesta periódica | launchd (abajo) |

Ingesta automática cada 30 min (procesa solo lo nuevo, es idempotente) —
`~/Library/LaunchAgents/com.sein.rag.ingest.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.sein.rag.ingest</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/docker</string>
    <string>compose</string>
    <string>--profile</string><string>ingest</string>
    <string>run</string><string>--rm</string><string>ingest</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/TU_USUARIO/sein_free_users_contracts_rag</string>
  <key>StartInterval</key><integer>1800</integer>
  <key>StandardOutPath</key><string>/tmp/sein-rag-ingest.log</string>
  <key>StandardErrorPath</key><string>/tmp/sein-rag-ingest.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.sein.rag.ingest.plist
```

## Paso 4 — Operación

```bash
docker compose ps                        # estado + healthchecks
curl -s localhost:8080/api/health | jq   # qdrant/ollama/modelos ok?
make logs                                # logs JSON del backend
docker compose --profile ingest run --rm ingest   # ingesta manual
```

Señales de problema típicas:

- `health.status = degraded` + `missing_models` → `make models`.
- Respuestas lentas con usuarios concurrentes → es esperado: un solo LLM de
  4B atiende en serie. El rate limit de nginx mantiene la cola corta.
- Memoria: qwen3:4b (~2.6GB) + bge-m3 (~1.2GB) + Qdrant + contenedores
  ≈ 6-7GB; deja `OLLAMA_KEEP_ALIVE=10m` (default) para que los modelos se
  descarguen de RAM tras inactividad si el equipo hace otras tareas (OCR).

## Actualizar la app

```bash
git pull
docker compose --profile public up -d --build   # rebuild + rolling restart
```
