FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY app app
COPY bot bot
COPY migrations migrations
COPY alembic.ini ./
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 staticloot
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY migrations migrations
COPY alembic.ini ./
USER staticloot
CMD ["static-loot-bot"]