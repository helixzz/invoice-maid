FROM node:20-slim AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS backend

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/backend

RUN apt-get update \
    && apt-get install -y --no-install-recommends libzbar0 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ ./
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
RUN mkdir -p /app/frontend \
    && ln -s /app/backend/frontend/dist /app/frontend/dist

RUN python -m pip install --upgrade pip setuptools wheel \
    && pip install -e . \
    && (pip install -e ".[full]" || true)

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
