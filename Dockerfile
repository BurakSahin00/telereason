# One container: FastAPI in fake mode serving the built frontend from the same origin.
# The droplet keeps its ROCm container and takes only the frontend-dist stage (infra/droplet.md).

FROM node:22-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Only the built files, for `docker build --target frontend-dist --output ...`.
FROM scratch AS frontend-dist
COPY --from=frontend-build /app/frontend/dist/ /

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# Same layout as the repo: loader.py finds fixtures/ three levels above itself.
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-cache
COPY backend/telereason ./telereason
COPY fixtures /app/fixtures
COPY --from=frontend-dist / /app/frontend/dist

RUN useradd --create-home --uid 10001 telereason
USER telereason

ENV PATH="/app/backend/.venv/bin:$PATH" \
    ENGINE=fake \
    FRONTEND_DIST=/app/frontend/dist
EXPOSE 8000

# The slim image has no curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["sh", "-c", "python -c \"import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/api/health', timeout=4)\""]

# One worker: the engine cannot be shared across processes.
CMD ["sh", "-c", "exec uvicorn telereason.api.main:app --workers 1 --host 0.0.0.0 --port ${PORT:-8000}"]
