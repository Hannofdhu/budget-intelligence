FROM ghcr.io/astral-sh/uv:0.11.26 AS uv

FROM node:24-bookworm-slim AS guancli-runtime
RUN npm install --global @guandata/guanskill@0.1.14

FROM python:3.12-slim AS python-runtime
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY apps ./apps
COPY packages ./packages
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

FROM python-runtime AS api
EXPOSE 8000
CMD ["budget-api"]

FROM python-runtime AS worker
USER root
COPY --from=guancli-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=guancli-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/@guandata/guanskill/bin/run.js /usr/local/bin/guancli
USER appuser
CMD ["budget-worker"]
