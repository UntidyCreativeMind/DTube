FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/ .
RUN npm ci --no-audit --no-fund && npm run build

FROM denoland/deno:bin-2.5.6 AS deno

FROM python:3.13-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=deno /deno /usr/local/bin/deno
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/dtube ./dtube
COPY --from=frontend /frontend/dist ./static

ENV HOME=/config \
    XDG_CACHE_HOME=/config/cache \
    DENO_DIR=/config/cache/deno \
    PYTHONUNBUFFERED=1

EXPOSE 3434
CMD ["python", "-m", "dtube.boot"]
