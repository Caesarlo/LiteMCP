# LiteMCP backend application image (M0-BOOT-001).
#
# This is the single application image shared by the compose `backend` and
# `worker` services (docs/architecture/00-overview.md §5.3): the two services
# differ only in their start command. The image CMD below runs the FastAPI
# app via uvicorn; the worker service overrides the command to run the
# placeholder `python -m litemcp.workers` entrypoint.
#
# Note: backend/pyproject.toml uses `uv_build` as its PEP 517 build backend,
# which requires `uv` at image build time. We install uv first, then
# `uv sync --frozen --no-dev` resolves the locked dependencies and builds +
# installs the `litemcp` package into a project virtualenv at /app/.venv.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# uv is required to build the `uv_build`-backed package and to install the
# locked dependency set. Version pinned to the repo's local uv toolchain.
COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /uvx /bin/

# Dependencies are installed before the source is copied so that pyproject /
# lockfile changes reuse the dependency layer. The project itself is also
# installed here (uv builds the wheel from src/ via uv_build), which puts
# the `litemcp` console script and importable package on the venv PATH.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# Default runtime: FastAPI app served by uvicorn on :8000.
EXPOSE 8000
CMD ["uvicorn", "litemcp.main:app", "--host", "0.0.0.0", "--port", "8000"]
