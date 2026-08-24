# syntax=docker/dockerfile:1
#
# Etapa 1: build de Vite. Etapa 2: nginx sirve estáticos y proxea /api.
# Bases pinneadas por digest para que el sistema base no cambie en silencio.

FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS builder

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build


FROM nginx:1.29-alpine@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80

# El healthcheck anterior solo probaba que nginx sirviera index.html desde
# disco: con el backend caído seguía dando "healthy" mientras cada usuario
# recibía 502 en su primera pregunta. Ahora atraviesa el proxy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -qO- http://127.0.0.1/api/health >/dev/null || exit 1
