# 빌드 컨텍스트는 backend/: docker build -f services/incident/Dockerfile .
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY . .
RUN uv sync --frozen --no-dev --no-editable --package ax-svc-incident

EXPOSE 8107
CMD ["uvicorn", "svc_incident.main:app", "--host", "0.0.0.0", "--port", "8107"]
