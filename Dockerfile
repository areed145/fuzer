FROM node:24-slim AS ui-build

WORKDIR /app/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM python:3.12-slim

RUN pip install --no-cache-dir uv
WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev
COPY --from=ui-build /app/ui/dist /app/ui/dist

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

CMD ["sh", "-c", "uv run --no-sync uvicorn fuzer.api:app --host 0.0.0.0 --port ${PORT:-10000}"]
