# The orchestrator's image, built by .gitea/workflows/images.yaml as
# gitea.sdlc.internal/platform/orchestrator:<commit>. Dependencies come from uv.lock exactly.
FROM docker.io/library/python:3.13.18-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.11.22 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev
ENV PATH=/app/.venv/bin:$PATH
ARG COMMIT=unknown
ENV ORCHESTRATOR_COMMIT=$COMMIT
USER 1000
ENTRYPOINT ["orchestrator"]
CMD ["serve"]
