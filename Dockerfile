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
RUN uv sync --frozen --no-dev --package fuzer-fakeplant \
    && uv run --no-sync fakeplant generate --asset okan --out /app/fakeplant/data/okan
COPY --from=ui-build /app/ui/dist /app/ui/dist

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    FUZER_PACK=okan \
    FUZER_DATA_DIR=/app/fakeplant/data/okan \
    FUZER_TIMESERIES=synthetic \
    DAGSTER_HOME=/var/lib/fuzer/dagster

CMD ["python", "-m", "fuzer.cli.deploy"]
