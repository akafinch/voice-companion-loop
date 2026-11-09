# The bot's image: the Python bot, serving the pages built in the first stage.

FROM node:24-slim AS pages
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
WORKDIR /app
# Exactly the locked dependencies, without the test tools. The project has no build system, so
# uv installs only its dependencies, into /app/.venv.
ENV UV_NO_DEV=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --locked
COPY companion/ companion/
COPY --from=pages /web/dist web/dist
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8080
# uvicorn listens on 127.0.0.1 unless told otherwise, which nothing outside the container reaches.
CMD ["uvicorn", "companion.app:app", "--host", "0.0.0.0", "--port", "8080"]
